#!/usr/bin/env python3
"""agi_error_pattern_learner.py — автономный предсказатель ошибок.

Читает supervisor лог и session логи, выявляет повторяющиеся паттерны ошибок,
сохраняет в patterns.json. Вызывается proactive_scan.py для предупреждений.
"""
import json, re, os, time
from pathlib import Path
from collections import Counter

PATTERNS_FILE = Path("/root/.hermes/data/error_patterns.json")
SUPERVISOR_LOG = Path("/root/.hermes/SUPERVISOR_LOG.md")
SESSION_DIR = Path("/root/.hermes/session")

# Известные паттерны ошибок (расширяются автоматически)
KNOWN_PATTERNS = {
    "connection_refused": r"(Connection refused|connect ECONNREFUSED|ECONNREFUSED)",
    "gateway_timeout": r"(Gateway Timeout|504|upstream timed out)",
    "api_rate_limit": r"(429|rate.?limit|too many requests)",
    "disk_full": r"(No space left on device|ENOSPC)",
    "auth_failure": r"(401|Unauthorized|Invalid API key|auth.*fail)",
    "mcp_crash": r"(MCP.*error|mcp.*timeout|mcp.*disconnect)",
    "config_corrupt": r"(config.*corrupt|yaml.*error|toml.*error|JSONDecodeError)",
    "docker_failure": r"(docker.*error|container.*exit|Cannot connect to Docker)",
    "git_conflict": r"(merge conflict|CONFLICT|would be overwritten)",
    "process_killed": r"(OOM|killed|exit code 137|SIGKILL)",
}

PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)


def scan_logs(max_lines=2000) -> dict:
    """Сканирует логи, извлекает паттерны ошибок."""
    matches = Counter()
    files_to_scan = []

    if SUPERVISOR_LOG.exists():
        files_to_scan.append(SUPERVISOR_LOG)

    if SESSION_DIR.exists():
        for f in sorted(SESSION_DIR.glob("session_*.json"), reverse=True)[:5]:
            files_to_scan.append(f)

    for f in files_to_scan:
        try:
            content = str(f.read_text())[-max_lines * 200:]
            for name, pattern in KNOWN_PATTERNS.items():
                found = len(re.findall(pattern, content, re.IGNORECASE))
                if found:
                    matches[name] += found
        except Exception:
            continue

    return dict(matches.most_common())


def learn_new_patterns(content: str, min_occurrences: int = 3) -> list:
    """Находит новые повторяющиеся строки ошибок."""
    error_lines = re.findall(
        r"(?:ERROR|WARN|FATAL|Traceback|Exception|fail|crash)[:\s].{10,120}",
        content, re.IGNORECASE
    )
    line_counts = Counter(error_lines)
    new_patterns = []
    for line, count in line_counts.most_common():
        if count >= min_occurrences:
            normalized = re.sub(r'\d+', 'N', line)[:80]
            normalized = re.sub(r'0x[0-9a-f]+', '0xADDR', normalized, re.IGNORECASE)
            new_patterns.append({"pattern": normalized, "occurrences": count})
    return new_patterns


def predict_risks() -> list:
    """Предсказывает вероятные проблемы на основе истории."""
    if not PATTERNS_FILE.exists():
        return []

    data = json.loads(PATTERNS_FILE.read_text())
    history = data.get("history", [])

    if len(history) < 3:
        return []

    # Простые эвристики
    risks = []
    streaks = data.get("streaks", {})

    for pattern, count in streaks.items():
        if count >= 3:
            risks.append({
                "risk": "high",
                "pattern": pattern,
                "message": f"Паттерн '{pattern}' повторяется {count} раз(а). Вероятен повтор.",
                "suggestion": "Проверить соответствующие сервисы."
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
    matches = scan_logs()

    # Загружаем историю
    if PATTERNS_FILE.exists():
        data = json.loads(PATTERNS_FILE.read_text())
    else:
        data = {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}

    # Обновляем streaks
    streaks = data.get("streaks", {})
    for name, count in matches.items():
        streaks[name] = streaks.get(name, 0) + count
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
    # Оставляем последние 100 записей
    data["history"] = data["history"][-100:]

    # Пробуем обучить новые паттерны
    if SUPERVISOR_LOG.exists():
        content = SUPERVISOR_LOG.read_text()[-50000:]
        new_pats = learn_new_patterns(content)
        if new_pats:
            data["learned_patterns"] = (data.get("learned_patterns", []) + new_pats)[-20:]

    data["last_update"] = timestamp

    with open(PATTERNS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {
        "status": "ok",
        "matches": matches,
        "streaks": streaks,
        "risks": predict_risks(),
        "learned": len(data.get("learned_patterns", [])),
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
            lines.append(f"    {r['risk'].upper()}: {r.get('message', '')}")

    if not result["matches"] and not result["risks"]:
        lines.append("  ✅ Новых паттернов ошибок не найдено.")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_report())
