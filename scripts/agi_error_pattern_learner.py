#!/usr/bin/env python3
"""agi_error_pattern_learner.py — автономный предсказатель ошибок.

v2 (02.08.2026): сканирует РЕАЛЬНЫЕ логи (/root/.hermes/logs/*.log), а не
SUPERVISOR_LOG.md (там только сводки «0 ошибок»). Самообучение: новые
повторяющиеся паттерны сохраняются в patterns.json и используются при
следующих сканах (learned_* паттерны). Добавлены топ-паттерны
file_not_found и gateway_already_running, которых не хватало.

Интеграция: вызывается proactive_scan.py и self_directed_queue.py.
"""
import json, re, time, hashlib
from pathlib import Path
from collections import Counter

PATTERNS_FILE = Path("/root/.hermes/data/error_patterns.json")
SUPERVISOR_LOG = Path("/root/.hermes/SUPERVISOR_LOG.md")
SESSION_DIR = Path("/root/.hermes/session")
LOG_DIR = Path("/root/.hermes/logs")

# Какие лог-файлы сканировать. mcp-stderr.log = 12MB → только хвост.
LOG_FILES = ["errors.log", "agent.log", "gateway.log", "mcp-stderr.log"]
TAIL_BYTES = 2_000_000  # 2MB хвоста на файл

# Известные паттерны ошибок (расширяются автоматически через learn_new_patterns)
KNOWN_PATTERNS = {
    "file_not_found": r"(No such file or directory|FileNotFoundError|ENOENT)",
    "gateway_already_running": r"(already running|EADDRINUSE|address already in use)",
    "connection_refused": r"(Connection refused|connect ECONNREFUSED|ECONNREFUSED)",
    "gateway_timeout": r"(Gateway Timeout|504|upstream timed out)",
    "request_timeout": r"(Request timed out|timed out|timeout)",
    "api_rate_limit": r"(429|rate.?limit|too many requests)",
    "disk_full": r"(No space left on device|ENOSPC)",
    "auth_failure": r"(401|Unauthorized|Invalid API key|auth.*fail)",
    "mcp_crash": r"(MCP.*error|mcp.*timeout|mcp.*disconnect|ClosedResourceError|keepalive failed)",
    "config_corrupt": r"(config.*corrupt|yaml.*error|toml.*error|JSONDecodeError)",
    "docker_failure": r"(docker.*error|container.*exit|Cannot connect to Docker)",
    "git_conflict": r"(merge conflict|CONFLICT|would be overwritten)",
    "process_killed": r"(OOM|killed|exit code 137|SIGKILL)",
}

# Конкретные рекомендации для известных паттернов
_SUGGESTIONS = {
    "file_not_found": "Проверить пути в скриптах/конфигах — файл не найден",
    "gateway_already_running": "Убить старый gateway (gateway.pid) перед рестартом",
    "gateway_timeout": "Проверить upstream/nginx — 504: gateway не отвечает в срок",
    "connection_refused": "Проверить порт/сервис (nc -z)",
    "request_timeout": "Проверить сеть/таймауты API",
    "api_rate_limit": "Увеличить backoff или подождать",
    "mcp_crash": "Перезапустить MCP-сервер",
    "disk_full": "Запустить disk-cleanup",
    "auth_failure": "Проверить API-ключи во всех .env",
    "config_corrupt": "Восстановить config.yaml из бэкапа",
    "docker_failure": "Проверить docker daemon",
    "git_conflict": "Разрешить merge conflict",
    "process_killed": "Проверить OOM/память",
}

MAX_LEARNED = 20
MIN_OCCURRENCES = 3

try:
    PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
except OSError:
    # Данные-директория не writable (cron-песочница, read-only mount):
    # импорт НЕ должен падать — чтение/тесты/--json работают, запись
    # упадёт позже с явной ошибкой в update_patterns.
    pass


def _tail_text(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    """Прочитать хвост файла, не грузя целиком (mcp-stderr.log = 12MB)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _learned_name(pattern: str) -> str:
    """Стабильное имя выученного паттерна (hash стабилен между запусками)."""
    return "learned_" + hashlib.md5(pattern.encode()).hexdigest()[:8]


def _load_data() -> dict:
    if PATTERNS_FILE.exists():
        try:
            return json.loads(PATTERNS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}


def _active_patterns(data: dict) -> dict:
    """KNOWN_PATTERNS + выученные паттерны (literal → regex)."""
    pats = dict(KNOWN_PATTERNS)
    for lp in data.get("learned_patterns", []):
        pats[lp["name"]] = re.escape(lp["pattern"])
    return pats


def scan_logs(data: dict, max_lines=2000) -> dict:
    """Сканирует логи, извлекает паттерны ошибок.

    Считает СТРОКИ с ошибкой (не совпадения): если строка содержит два
    альтернативных матча паттерна (FileNotFoundError + No such file...),
    она учитывается один раз.
    """
    matches = Counter()
    files_to_scan = []
    if SUPERVISOR_LOG.exists():
        files_to_scan.append(SUPERVISOR_LOG)
    for name in LOG_FILES:
        p = LOG_DIR / name
        if p.exists():
            files_to_scan.append(p)
    if SESSION_DIR.exists():
        files_to_scan.extend(sorted(SESSION_DIR.glob("session_*.json"), reverse=True)[:5])

    compiled = {name: re.compile(p, re.IGNORECASE) for name, p in _active_patterns(data).items()}
    for f in files_to_scan:
        if f == SUPERVISOR_LOG:
            content = str(f.read_text())[-max_lines * 200:]
        else:
            content = _tail_text(f)
        lines = content.splitlines()
        for name, rx in compiled.items():
            if name.startswith("learned_"):
                # Выученные паттерны хранятся в НОРМАЛИЗОВАННОМ виде (числа→N,
                # truncated) — матчить их по нормализованной строке, иначе они
                # никогда не совпадут с сырым логом (фикс 03.08: самообучение
                # было мёртвым, learned_* не попадали в streaks → не кормили
                # предсказатель рисков).
                found = sum(1 for line in lines if rx.search(_normalize_line(line)))
            else:
                found = sum(1 for line in lines if rx.search(line))
            if found:
                matches[name] += found
    return dict(matches.most_common())


def _normalize_line(line: str) -> str:
    """Нормализует строку ошибки: срезает уровень+[id]+модуль, схлопывает
    check_* функции и числа — чтобы 20 вариантов registry-шума стали 1."""
    n = line[:200]
    # Сначала hex-адреса: (а) flags= — 4-й позиционный аргумент re.sub это
    # count, IGNORECASE туда уходил и uppercase-адреса (0xABCDEF01) НЕ
    # схлопывались; (б) после замены цифр на N паттерн 0x[0-9a-f]+ мёртв.
    n = re.sub(r"0x[0-9a-f]+", "0xADDR", n, flags=re.IGNORECASE)
    n = re.sub(r"\d+", "N", n)[:140]
    # "0" в "0xADDR" превратился в N — вернуть маркер
    n = n.replace("NxADDR", "0xADDR")
    n = re.sub(r"^(?:WARNING|ERROR|INFO|DEBUG|FATAL)\s+", "", n)
    n = re.sub(r"^\[\S+\]\s+\S+:\s", "", n)
    n = re.sub(r"\bcheck_\w+\b", "check_FN", n)
    return n.strip()[:80]


def learn_new_patterns(data: dict, min_occurrences: int = MIN_OCCURRENCES) -> list:
    """Находит новые повторяющиеся строки ошибок, возвращает новые learned."""
    known_names = set(KNOWN_PATTERNS.keys()) | {lp["name"] for lp in data.get("learned_patterns", [])}

    text = ""
    for name in LOG_FILES:
        p = LOG_DIR / name
        if p.exists():
            text += _tail_text(p) + "\n"

    error_lines = re.findall(
        r"(?:ERROR|WARNING|WARN|FATAL|Traceback|Exception|fail|crash)[:\s].{10,120}",
        text, re.IGNORECASE,
    )
    # Считаем по НОРМАЛИЗОВАННОЙ строке (шум registry схлопывается в 1 паттерн)
    line_counts = Counter(_normalize_line(l) for l in error_lines)
    new_learned = []
    for normalized, count in line_counts.most_common(50):
        if count < min_occurrences:
            continue
        name = _learned_name(normalized)
        if name in known_names:
            continue
        new_learned.append({
            "name": name,
            "pattern": normalized,
            "occurrences": count,
            "first_seen": time.time(),
        })
    return new_learned


def _pattern_trend(data: dict, pattern: str, window: int = 6) -> str:
    """Тренд появления паттерна по последним сканам: rising / stable / falling / new.

    Сравниваем ПРИСУТСТВИЕ (count > 0) в первой и второй половине окна:
    вторая > первой → растёт, < → падает, = → стабилен. Падающий паттерн
    (последние сканы чистые) реально менее рискован, чем показывает streak,
    который лишь декрементится и может долго держаться на 3+.
    """
    counts = [h.get("patterns", {}).get(pattern, 0) for h in data.get("history", [])][-window:]
    present = [1 if c > 0 else 0 for c in counts]
    total = sum(present)
    if total == 0:
        return "new"
    if len(present) < 2:
        return "stable"
    half = len(present) // 2
    first_half = sum(present[:half])
    second_half = sum(present[half:])
    if second_half > first_half:
        return "rising"
    if second_half < first_half:
        return "falling"
    return "stable"


def predict_risks(data: dict) -> list:
    """Предсказывает вероятные проблемы на основе истории.

    HIGH — только для растущих/стабильных паттернов; падающие (пропадают
    из свежих сканов) понижаются до low, чтобы не засорять очередь задач.
    """
    history = data.get("history", [])
    if len(history) < 3:
        return []

    risks = []
    for pattern, count in data.get("streaks", {}).items():
        if count >= 3:
            trend = _pattern_trend(data, pattern)
            risk = "high" if trend != "falling" else "low"
            risks.append({
                "risk": risk,
                "pattern": pattern,
                "trend": trend,
                "message": (f"Паттерн '{pattern}' встречался в {count} последних сканах "
                            f"(тренд: {trend}). Вероятен повтор."),
                "suggestion": _SUGGESTIONS.get(pattern, "Проверить соответствующие сервисы."),
            })

    # Проверить время с последнего сбоя
    last_errors = [h for h in history if h.get("error_count", 0) > 0]
    if last_errors:
        last_ts = last_errors[-1].get("timestamp", 0)
        hours_ago = (time.time() - last_ts) / 3600
        if hours_ago > 24:
            risks.append({
                "risk": "low",
                "message": f"Система стабильна {hours_ago:.0f} часов. Плановый аудит рекомендован.",
            })
    return risks


def update_patterns() -> dict:
    """Главная функция: сканирует, обновляет, предсказывает."""
    timestamp = time.time()
    data = _load_data()
    matches = scan_logs(data)

    # Обновляем streaks — СЧЁТЧИК СКАНОВ, где паттерн присутствовал (не сырые
    # совпадения: при статичном errors.log они росли бы бесконечно).
    streaks = data.get("streaks", {})
    for name in matches:
        streaks[name] = min(streaks.get(name, 0) + 1, 50)
    # Ослабляем те что не появились
    for name in list(streaks.keys()):
        if name not in matches:
            streaks[name] = max(0, streaks.get(name, 0) - 1)
    data["streaks"] = streaks

    # Добавляем в историю
    data["history"].append({
        "timestamp": timestamp,
        "error_count": sum(matches.values()),
        "patterns": matches,
    })
    data["history"] = data["history"][-100:]

    # Самообучение: новые паттерны из логов
    prev_total = len(data.get("learned_patterns", []))
    new_pats = learn_new_patterns(data)
    added = 0
    if new_pats:
        merged = (data.get("learned_patterns", []) + new_pats)[-MAX_LEARNED:]
        added = len(merged) - prev_total
        data["learned_patterns"] = merged

    # Тренды и риски персистим в файл — потребители (self_directed_queue)
    # читают ГОТОВЫЙ trend-aware результат, не скатываясь в наивный
    # streak-логик (фикс: очередь всё ещё плодила 8 одинаковых задач
    # "streak: 7", хотя predict_risks уже умел тренды).
    risks = predict_risks(data)
    data["trends"] = {r["pattern"]: r["trend"] for r in risks if r.get("pattern")}
    data["risks"] = risks
    data["last_update"] = timestamp
    with open(PATTERNS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {
        "status": "ok",
        "matches": matches,
        "streaks": streaks,
        "risks": risks,
        "learned_total": len(data.get("learned_patterns", [])),
        "learned_new": added,
    }


def get_report() -> str:
    """Человекочитаемый отчёт."""
    result = update_patterns()
    lines = ["📊 Error Pattern Learner:"]

    if result["matches"]:
        lines.append("  🔍 Найдены паттерны:")
        for name, count in result["matches"].items():
            emoji = "🔴" if count > 5 else "🟡" if count > 1 else "⚪"
            lines.append(f"    {emoji} {name}: {count}")

    if result["risks"]:
        lines.append("  ⚠️ Риски:")
        for r in result["risks"]:
            trend = f" ({r.get('trend', '?')})" if r.get("trend") else ""
            lines.append(f"    {r['risk'].upper()}{trend}: {r.get('message', '')}")
            if r.get("suggestion"):
                lines.append(f"      → {r['suggestion']}")

    if result["learned_new"]:
        lines.append(f"  🧬 Выучено новых паттернов: {result['learned_new']} (всего {result['learned_total']})")

    if not result["matches"] and not result["risks"]:
        lines.append("  ✅ Новых паттернов ошибок не найдено.")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print(json.dumps(update_patterns(), indent=2, ensure_ascii=False))
    else:
        print(get_report())
