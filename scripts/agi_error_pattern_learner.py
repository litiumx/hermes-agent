#!/usr/bin/env python3
"""agi_error_pattern_learner.py — автономный предсказатель ошибок.

v7 (13.08.2026): DECAY пар со-встречаемостей (grow point 12.08: «пары
co-occurrences тоже взвешивать по recency»). _learn_cooccurrences и
_learn_module_pairs считают вес скана 0.5^(age/half_life) как в
_decay_scores: вчерашняя пара весит 1.0, месячная — доли, устаревшие
пары выпадают за порог min_pairs сами. decay=False — старое поведение
(+1 за скан, сырые целые счётчики). co_score/сообщения companions
показывают «вес» вместо «числа сканов».

v6 (12.08.2026): временной DECAY паттернов (grow point 12.08: «старые
streaks весят меньше свежих»). _decay_scores считает recency-взвешенный
score присутствия: каждый скан истории даёт вес 0.5^(age/half_life) —
вчерашний скан ≈1.0, двухмесячный ≈0. Стреак, бушевавший давно, больше
не даёт HIGH-риск: predict_risks требует decay_score >= RISK_DECAY_FLOOR
(2.0) для high, иначе low. decay_score пишется в каждый риск и
персистится в patterns.json (decay_scores) для потребителей очереди.

v5 (11.08.2026): контекст по МОДУЛЮ (grow point 11.08: «какой лог-файл/
сервис дал ошибку — сузить предсказания до сервиса»). scan_logs_by_source
считает матчи ПО ИСТОЧНИКАМ (errors.log, gateway.log, ...), история сканов
хранит "sources": {pattern: [file]}, _learn_module_pairs строит пары
со-встречаемостей ВНУТРИ одного модуля, predict_module_companions
предсказывает: «если A сейчас в gateway.log, B исторически приходил в том
же gateway.log — вероятен следующим». Агрегат scan_logs не изменился
(backward compat: сумма по источникам).

v4 (11.08.2026): предсказание по КОНТЕКСТУ (grow point 11.08: «не только
по тексту ошибки»). Из истории сканов строится карта пар со-встречаемостей:
если паттерн A исторически приходил в одном скане с B (gateway_timeout +
connection_refused), то при обнаружении A в свежем скане B предсказывается
как вероятный следующий (predict_companions). Карта пар и прогноз
персистятся в patterns.json (cooccurrences / companions).

v3 (08.08.2026): жизненный цикл learned-паттернов. Раньше выученный паттерн
навсегда занимал слот MAX_LEARNED и никогда не обновлялся: occurrences
застывали на первом обнаружении, мёртвые регрессии вечно матчились в логах.
Теперь: повторное появление обновляет occurrences/last_seen (refresh_learned),
а паттерны без появлений 14+ дней вычищаются (_prune_stale_learned).

v2 (02.08.2026): сканирует РЕАЛЬНЫЕ логи (/root/.hermes/logs/*.log), а не
SUPERVISOR_LOG.md (там только сводки «0 ошибок»). Самообучение: новые
повторяющиеся паттерны сохраняются в patterns.json и используются при
следующих сканах (learned_* паттерны). Добавлены топ-паттерны
file_not_found и gateway_already_running, которых не хватало.

Интеграция: вызывается proactive_scan.py и self_directed_queue.py.
"""
import json, os, re, time, hashlib
from pathlib import Path
from collections import Counter

# Пути данных — env-переопределяемые (паттерн цикла 8: HERMES_HOME задаёт
# базу, AGI_*_FILE / AGI_*_DIR — точные пути; песочница без прав на /root).
HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
PATTERNS_FILE = Path(os.environ.get("AGI_PATTERNS_FILE",
                                    os.path.join(HERMES_HOME, "data/error_patterns.json")))
SUPERVISOR_LOG = Path(os.environ.get("AGI_SUPERVISOR_LOG",
                                     os.path.join(HERMES_HOME, "SUPERVISOR_LOG.md")))
SESSION_DIR = Path(os.environ.get("AGI_SESSION_DIR",
                                  os.path.join(HERMES_HOME, "session")))
LOG_DIR = Path(os.environ.get("AGI_LOG_DIR",
                              os.path.join(HERMES_HOME, "logs")))

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
LEARNED_TTL_DAYS = 14  # learned-паттерн без появлений дольше этого — удаляется
COOCCUR_MIN_PAIRS = 2   # пара паттернов считается значимой после N совместных сканов
MAX_COMPANIONS = 3      # сколько парных паттернов предсказывать за раз
STREAK_HALF_LIFE_DAYS = 14  # v6: вес присутствия в скане падает вдвое за N дней
RISK_DECAY_FLOOR = 2.0      # v6: min decay_score для риска high (2 свежих скана)
# v8: петля обратной связи companion (цикл 26) — после исполнения пре-емптивной
# fix-задачи планировщик сообщает, подтвердился ли предсказанный паттерн.
FEEDBACK_BOOST = 1.0      # подтверждённый companion: +1 к весу пар (anchor, pattern)
FEEDBACK_PENALTY = 0.5    # опровергнутый: -0.5 к весу пар (floor 0, удаление < min_pairs)
FEEDBACK_JOURNAL_MAX = 50 # кап журнала фидбеков в patterns.json
RISK_FEEDBACK_PENALTY = 2 # опровергнутый риск: -2 к streak (порог high = 3)

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
    """Сканирует логи, извлекает паттерны ошибок (агрегат по источникам).

    Считает СТРОКИ с ошибкой (не совпадения): если строка содержит два
    альтернативных матча паттерна (FileNotFoundError + No such file...),
    она учитывается один раз. v5: агрегат = сумма по scan_logs_by_source,
    чтобы источники и агрегат никогда не расходились.
    """
    matches = Counter()
    for src, pats in scan_logs_by_source(data, max_lines).items():
        for name, count in pats.items():
            matches[name] += count
    return dict(matches.most_common())


def scan_logs_by_source(data: dict, max_lines=2000) -> dict:
    """Сканирует логи, возвращает матчи ПО ИСТОЧНИКАМ (лог-файл/сервис).

    Структура: {source_name: {pattern: count}}. source_name — имя лог-файла
    (errors.log, gateway.log, ...), "supervisor" для SUPERVISOR_LOG или
    "session" для сессионных файлов. Источники без матчей отсутствуют
    в результате. На основе этого v5 предсказывает парные паттерны в
    пределах ОДНОГО модуля (predict_module_companions).
    """
    by_source = {}
    files_to_scan = []
    if SUPERVISOR_LOG.exists():
        files_to_scan.append(("supervisor", SUPERVISOR_LOG))
    for name in LOG_FILES:
        p = LOG_DIR / name
        if p.exists():
            files_to_scan.append((name, p))
    if SESSION_DIR.exists():
        for sp in sorted(SESSION_DIR.glob("session_*.json"), reverse=True)[:5]:
            files_to_scan.append(("session", sp))

    compiled = {name: re.compile(p, re.IGNORECASE) for name, p in _active_patterns(data).items()}
    for src, f in files_to_scan:
        if f == SUPERVISOR_LOG:
            content = str(f.read_text())[-max_lines * 200:]
        else:
            content = _tail_text(f)
        lines = content.splitlines()
        src_counter = Counter()
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
                src_counter[name] = found
        if src_counter:
            by_source[src] = dict(src_counter)
    return by_source


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


def _collect_error_counts() -> Counter:
    """Собирает нормализованные счётчики повторяющихся строк ошибок из логов."""
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
    return Counter(_normalize_line(l) for l in error_lines)


def learn_new_patterns(data: dict, min_occurrences: int = MIN_OCCURRENCES) -> list:
    """Находит новые повторяющиеся строки ошибок, возвращает новые learned."""
    known_names = set(KNOWN_PATTERNS.keys()) | {lp["name"] for lp in data.get("learned_patterns", [])}

    new_learned = []
    now = time.time()
    for normalized, count in _collect_error_counts().most_common(50):
        if count < min_occurrences:
            continue
        name = _learned_name(normalized)
        if name in known_names:
            continue
        new_learned.append({
            "name": name,
            "pattern": normalized,
            "occurrences": count,
            "first_seen": now,
            "last_seen": now,
        })
    return new_learned


def refresh_learned(data: dict, counts: Counter = None) -> int:
    """Обновляет occurrences/last_seen у известных learned-паттернов.

    Раньше повторное появление выученного паттерна не меняло его запись:
    occurrences застывали, и по данным нельзя было отличить активную
    регрессию от давно ушедшей. Возвращает число обновлённых паттернов.
    """
    if counts is None:
        counts = _collect_error_counts()
    by_name = {_learned_name(n): c for n, c in counts.items()}
    refreshed = 0
    now = time.time()
    for lp in data.get("learned_patterns", []):
        c = by_name.get(lp["name"])
        if c:
            lp["occurrences"] = lp.get("occurrences", 0) + c
            lp["last_seen"] = now
            refreshed += 1
    return refreshed


def _prune_stale_learned(data: dict, max_age_days: int = LEARNED_TTL_DAYS) -> int:
    """Удаляет learned-паттерны без появлений дольше max_age_days.

    Мёртвые регрессии не должны вечно занимать слоты MAX_LEARNED и матчиться
    в сканах. Для старых записей без last_seen используется first_seen —
    при следующем появлении refresh_learned проставит свежий last_seen.
    """
    cutoff = time.time() - max_age_days * 86400
    before = len(data.get("learned_patterns", []))
    data["learned_patterns"] = [
        lp for lp in data.get("learned_patterns", [])
        if lp.get("last_seen", lp.get("first_seen", 0)) >= cutoff
    ]
    return before - len(data["learned_patterns"])


def _scan_weight(h: dict, now: float = None,
                 half_life_days: float = STREAK_HALF_LIFE_DAYS) -> float:
    """Recency-вес скана для пар со-встречаемостей (v7).

    Тот же закон, что в _decay_scores: 0.5 ** (age / half_life) — свежий
    скан ≈1.0, на half-life 0.5, дальше экспоненциально меньше. Сканы без
    timestamp и с битым (будущим) timestamp трактуются как свежие
    (консервативно: не занижать вес). Округление до 4 знаков держит
    свежие сканы ровно 1.0 (целые счётчики не превращаются в 0.9999...).
    """
    if now is None:
        now = time.time()
    ts = h.get("timestamp")
    if ts is None:
        return 1.0
    age = now - ts
    if age <= 0:
        return 1.0
    return round(0.5 ** (age / (half_life_days * 86400)), 4)


def _learn_cooccurrences(data: dict, min_pairs: int = COOCCUR_MIN_PAIRS,
                         decay: bool = True) -> dict:
    """Строит карту парных со-встречаемостей паттернов из истории сканов.

    Для каждой записи истории (скана) все присутствующие паттерны (count>0)
    попарно инкрементятся в обе стороны — карта симметрична. v7: вес скана
    зависит от свежести (decay=True, по умолчанию) — 0.5^(age/half_life)
    как в _decay_scores: вчерашняя пара весит 1.0, месячная — доли.
    decay=False сохраняет старое поведение (+1 за скан, сырые счётчики).
    Пары с суммой весов < min_pairs отбрасываются: одна случайная встреча
    не паттерн. Записи без "patterns" (legacy/повреждённые) пропускаются.
    """
    now = time.time()
    pairs: dict = {}
    for h in data.get("history", []):
        pats = h.get("patterns") or {}
        names = [n for n, c in pats.items() if c > 0]
        w = _scan_weight(h, now) if decay else 1
        init = 0.0 if decay else 0
        for i in range(len(names)):
            a = names[i]
            for b in names[i + 1:]:
                pairs.setdefault(a, {}).setdefault(b, init)
                pairs[a][b] += w
                pairs.setdefault(b, {}).setdefault(a, init)
                pairs[b][a] += w
    out: dict = {}
    for a, bs in pairs.items():
        filtered = {b: round(c, 2) for b, c in bs.items() if c >= min_pairs}
        if filtered:
            out[a] = filtered
    return out


def predict_companions(data: dict, current_matches: dict,
                       min_pairs: int = COOCCUR_MIN_PAIRS,
                       max_companions: int = MAX_COMPANIONS) -> list:
    """Предсказывает паттерны, исторически приходящие ВМЕСТЕ с текущими.

    Контекст-предсказание (v4): если A в прошлом регулярно появлялся в одном
    скане с B, а сейчас найден только A — B вероятен следующим. Уже
    присутствующие паттерны исключаются. Возвращает до max_companions
    кандидатов, отсортированных по убыванию co_score (число совместных сканов).
    """
    present = {n for n, c in (current_matches or {}).items() if c > 0}
    if not present:
        return []
    co = data.get("cooccurrences")
    if not co:
        co = _learn_cooccurrences(data, min_pairs)
    scores: dict = {}
    for a in present:
        for b, cnt in (co.get(a) or {}).items():
            if b in present:
                continue
            scores[b] = scores.get(b, 0) + cnt
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:max_companions]
    return [{
        "pattern": b,
        "co_score": c,
        # v8: anchors — активные паттерны, породившие прогноз. Без них
        # обратная связь (feedback_companion) не знает, КАКИЕ пары усиливать/
        # ослаблять после исполнения fix-задачи (цикл 26).
        "anchors": sorted(a for a in present if (co.get(a) or {}).get(b)),
        "message": (f"Паттерн '{b}' исторически появляется вместе с текущими "
                    f"ошибками (вес {c:g}). Вероятен следующим."),
    } for b, c in ranked]


def _learn_module_pairs(data: dict, min_pairs: int = COOCCUR_MIN_PAIRS,
                        decay: bool = True) -> dict:
    """Пары со-встречаемостей паттернов ПО МОДУЛЯМ (лог-файлам).

    v5. Возвращает {source: {a: {b: count}}}: для каждой записи истории и
    каждого источника (лог-файла) паттерны, найденные в ЭТОМ источнике,
    попарно инкрементятся в обе стороны. В отличие от _learn_cooccurrences
    (пары в скане вообще), пара привязана к модулю: (a,b) в gateway.log
    значит «a и b приходили вместе именно в gateway.log». Прогноз по парам
    другого модуля исключён. v7: вес скана по свежести (decay=True,
    по умолчанию), decay=False — сырые счётчики. Пары с суммой весов
    < min_pairs отбрасываются. Записи без "sources" (legacy/повреждённые)
    пропускаются.
    """
    now = time.time()
    per_src: dict = {}
    for h in data.get("history", []):
        sources = h.get("sources") or {}
        w = _scan_weight(h, now) if decay else 1
        init = 0.0 if decay else 0
        for src, pats in sources.items():
            if not pats:
                continue
            names = [n for n, c in pats.items() if c > 0]
            pairs = per_src.setdefault(src, {})
            for i in range(len(names)):
                a = names[i]
                for b in names[i + 1:]:
                    pairs.setdefault(a, {}).setdefault(b, init)
                    pairs[a][b] += w
                    pairs.setdefault(b, {}).setdefault(a, init)
                    pairs[b][a] += w
    # Отбрасываем пары < min_pairs; источник держим только если после
    # фильтра остались пары (иначе {src: {}} засорял бы карту)
    out: dict = {}
    for src, pairs in per_src.items():
        filtered = {a: {b: round(c, 2) for b, c in bs.items() if c >= min_pairs}
                    for a, bs in pairs.items()
                    if any(c >= min_pairs for c in bs.values())}
        if filtered:
            out[src] = filtered
    return out


def predict_module_companions(data: dict, current_sources: dict,
                              min_pairs: int = COOCCUR_MIN_PAIRS,
                              max_companions: int = MAX_COMPANIONS) -> list:
    """Предсказывает паттерны, исторически приходящие в ТОТ ЖЕ модуль.

    v5. current_sources: {source: {pattern: count}} — результат свежего
    скана по источникам (scan_logs_by_source). Для каждого источника берутся
    пары, выученные ИМЕННО ДЛЯ ЭТОГО источника (module_cooccurrences[src]);
    кандидаты — парные паттерны присутствующих, ещё не найденные в этом
    модуле. Пары из других модулей в прогноз не попадают — предсказание
    сужено до сервиса. Возвращает до max_companions кандидатов
    {pattern, source, co_score}, отсортированных по убыванию co_score;
    source — модуль, где кандидат ожидается.
    """
    per_src = data.get("module_cooccurrences")
    if not per_src:
        per_src = _learn_module_pairs(data, min_pairs)
    # pattern -> (source, score): держим лучший (макс. co_score) источник
    best: dict = {}
    for src, pats in (current_sources or {}).items():
        present = {n for n, c in (pats or {}).items() if c > 0}
        if not present:
            continue
        pairs = per_src.get(src) or {}
        for a in present:
            for b, cnt in (pairs.get(a) or {}).items():
                if b in present:
                    continue
                cur = best.get(b)
                if cur is None or cnt > cur[1]:
                    # v8: anchors — активный паттерн в том же модуле, давший
                    # прогноз (для обратной связи feedback_companion, цикл 26)
                    best[b] = (src, cnt, [a])
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])[:max_companions]
    return [{
        "pattern": b,
        "source": src,
        "co_score": cnt,
        "anchors": anchors,
        "message": (f"Паттерн '{b}' исторически появляется в одном модуле с "
                    f"текущими ошибками (вес {cnt:g}). Ожидается в {src}."),
    } for b, (src, cnt, anchors) in ranked]


def _find_companion_entry(data: dict, pattern: str,
                          module: str | None = None) -> dict | None:
    """Найти запись companion (module или global) по паттерну.

    module задан → сначала module_companions (source == module), затем
    global companions как fallback (module-запись могла потеряться при
    пересборке, а anchors в global-версии всё равно валидны).
    """
    if module:
        for e in data.get("module_companions") or []:
            if (isinstance(e, dict) and e.get("pattern") == pattern
                    and e.get("source") == module):
                return e
    for e in data.get("companions") or []:
        if isinstance(e, dict) and e.get("pattern") == pattern:
            return e
    return None


def _adjust_pair(pairs: dict, a: str, b: str, delta: float) -> bool:
    """Скорректировать симметричную пару (a,b) на delta, floor 0.0."""
    if a not in pairs or b not in pairs[a]:
        return False
    pairs[a][b] = round(max(0.0, pairs[a][b] + delta), 2)
    pairs.setdefault(b, {})
    pairs[b][a] = round(max(0.0, pairs[b].get(a, 0.0) + delta), 2)
    return True


def _prune_pairs(pairs: dict, min_pairs: float = COOCCUR_MIN_PAIRS) -> int:
    """Удалить пары ниже min_pairs (как в _learn_cooccurrences); вернуть число удалённых.

    Симметричные карты чистим в обе стороны: если у a больше нет пар —
    ключ удаляется, иначе b мог бы висеть с пустым словарём.
    """
    removed = 0
    for a in list(pairs.keys()):
        kept = {b: c for b, c in pairs[a].items() if c >= min_pairs}
        removed += len(pairs[a]) - len(kept)
        if kept:
            pairs[a] = kept
        else:
            del pairs[a]
            removed += 1
    return removed


def feedback_companion(pattern: str, confirmed: bool,
                       module: str | None = None,
                       boost: float = FEEDBACK_BOOST,
                       penalty: float = FEEDBACK_PENALTY) -> dict:
    """Обратная связь по companion-предсказанию после исполнения fix-задачи.

    Замыкает петлю прогнозирования (grow point цикла 25): планировщик
    создал пре-емптивную задачу из предсказания learner'а — после
    исполнения (mark_completed в self_directed_queue) сообщаем,
    подтвердился ли паттерн.

    confirmed=True  → пары (anchor, pattern) в cooccurrences усиливаются
                      на boost (следующий прогноз даст больший co_score).
    confirmed=False → пары ослабляются на penalty (floor 0.0); пары ниже
                      COOCCUR_MIN_PAIRS удаляются — опровергнутый
                      companion выпадает из прогноза автоматически.
    module задаёт модульную карту (module_cooccurrences[module]) — global
    cooccurrences не трогаются. Записи без anchors (legacy) — fallback:
    корректируется co_score самой записи (переживёт до пересборки).
    Журнал ведётся в data["feedback"] (кап FEEDBACK_JOURNAL_MAX);
    неизвестный паттерн — no-op с error, без журнала.
    """
    delta = boost if confirmed else -penalty
    report = {"pattern": pattern, "module": module, "confirmed": confirmed,
              "delta": delta, "adjusted_pairs": 0}
    if not pattern or not isinstance(pattern, str):
        report["error"] = "empty pattern"
        return report
    data = _load_data()
    adjusted = 0
    entry = None

    if module:
        pairs = data.setdefault("module_cooccurrences", {}).get(module)
        if pairs:
            entry = _find_companion_entry(data, pattern, module)
            for a in (entry or {}).get("anchors") or []:
                if _adjust_pair(pairs, a, pattern, delta):
                    adjusted += 1
            if adjusted:
                data["module_cooccurrences"][module] = pairs
                _prune_pairs(pairs)
    else:
        pairs = data.setdefault("cooccurrences", {})
        entry = _find_companion_entry(data, pattern)
        for a in (entry or {}).get("anchors") or []:
            if _adjust_pair(pairs, a, pattern, delta):
                adjusted += 1
        if adjusted:
            _prune_pairs(pairs)

    # legacy-запись без anchors: правим co_score самой записи (fallback)
    if adjusted == 0 and entry is not None and "co_score" in entry:
        entry["co_score"] = round(max(0.0, entry.get("co_score", 0.0) + delta), 2)
        if entry["co_score"] <= 0.0:
            data["companions"] = [
                e for e in data.get("companions", [])
                if not (isinstance(e, dict) and e.get("pattern") == pattern)]
            if module:
                data["module_companions"] = [
                    e for e in data.get("module_companions", [])
                    if not (isinstance(e, dict) and e.get("pattern") == pattern
                            and e.get("source") == module)]
        report["co_score_adjusted"] = True

    report["adjusted_pairs"] = adjusted
    if adjusted == 0 and entry is None:
        report["error"] = "pattern not found"
        return report

    fb = data.setdefault("feedback", [])
    fb.append({"ts": time.time(), "pattern": pattern, "module": module,
               "confirmed": confirmed, "delta": delta, "adjusted_pairs": adjusted})
    data["feedback"] = fb[-FEEDBACK_JOURNAL_MAX:]
    try:
        with open(PATTERNS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        report["saved"] = True
    except OSError as e:
        report["saved"] = False
        report["error"] = str(e)
    return report


def feedback_risk(pattern: str, confirmed: bool,
                  penalty: float = RISK_FEEDBACK_PENALTY) -> dict:
    """Обратная связь по risk-задаче после исполнения run_next (цикл 30).

    Замыкает петлю прогнозирования рисков: планировщик создал fix-задачу
    из predict_risks (streak >= 3, тренд не falling) — после исполнения
    («Investigate and fix pattern: ...») сообщаем, проявился ли паттерн
    в выводе расследования.

    confirmed=True  → риск подтверждён (паттерн реально наблюдается):
                      streak НЕ трогаем — счётчик обновляет сам сканер
                      (update_patterns), ручное усиление дралось бы с ним.
    confirmed=False → паттерн не проявился → streak снижается на penalty
                      (floor 0). Одно опровержение сбрасывает streak 3→1 —
                      риск падает ниже порога high и перестаёт
                      генерировать задачи (не «вечный» ре-квеинг).
    Журнал ведётся в data["feedback"] (type="risk", кап FEEDBACK_JOURNAL_MAX);
    неизвестный паттерн — no-op с error, без журнала.
    """
    delta = 0.0 if confirmed else -penalty
    report = {"pattern": pattern, "confirmed": confirmed, "delta": delta}
    if not pattern or not isinstance(pattern, str):
        report["error"] = "empty pattern"
        return report
    data = _load_data()
    streaks = data.get("streaks", {})
    if pattern not in streaks:
        report["error"] = "pattern not found"
        return report
    before = streaks.get(pattern, 0)
    if not confirmed:
        streaks[pattern] = max(0, before - penalty)
    data["streaks"] = streaks
    report["streak_before"] = before
    report["streak_after"] = streaks[pattern]
    fb = data.setdefault("feedback", [])
    fb.append({"ts": time.time(), "type": "risk", "pattern": pattern,
               "confirmed": confirmed, "delta": delta,
               "streak_before": before, "streak_after": streaks[pattern]})
    data["feedback"] = fb[-FEEDBACK_JOURNAL_MAX:]
    try:
        with open(PATTERNS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        report["saved"] = True
    except OSError as e:
        report["saved"] = False
        report["error"] = str(e)
    return report


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


def _decay_scores(data: dict, half_life_days: float = STREAK_HALF_LIFE_DAYS) -> dict:
    """Recency-взвешенный score присутствия паттернов (v6).

    Каждый скан истории, где паттерн присутствовал (count > 0), даёт вес
    0.5 ** (age_days / half_life_days): свежий скан ≈1.0, на возрасте
    half_life — 0.5, дальше экспоненциально меньше. Старые streaks больше
    не весят как свежие: паттерн, бушевавший 2 месяца назад, даёт ~0, а
    вчерашний — почти полный вес. Считается ПРИСУТСТВИЕ, не объём (count=3
    в скане == count=1), чтобы score был сопоставим со streak. Сканы без
    timestamp трактуются как свежие (консервативно: не занижать вес).
    Записи без "patterns" (legacy/повреждённые) пропускаются молча.
    """
    now = time.time()
    hl = half_life_days * 86400
    scores: dict = {}
    for h in data.get("history", []):
        pats = h.get("patterns")
        if not pats:
            continue
        age = now - h.get("timestamp", now)
        if age < 0:  # битый timestamp в будущем — не занижаем вес
            age = 0
        w = 0.5 ** (age / hl)
        for name, count in pats.items():
            if count > 0:
                scores[name] = scores.get(name, 0.0) + w
    return scores


def predict_risks(data: dict) -> list:
    """Предсказывает вероятные проблемы на основе истории.

    HIGH — только для растущих/стабильных паттернов; падающие (пропадают
    из свежих сканов) понижаются до low. v6: временной decay — старый
    streak (появления давно, decay_score < RISK_DECAY_FLOOR) тоже даёт low:
    «встречался 3 раза месяц назад» ≠ активный риск. decay_score пишется
    в каждый риск для потребителей (self_directed_queue).
    """
    history = data.get("history", [])
    if len(history) < 3:
        return []

    decays = _decay_scores(data)
    risks = []
    for pattern, count in data.get("streaks", {}).items():
        if count >= 3:
            trend = _pattern_trend(data, pattern)
            decay = decays.get(pattern, 0.0)
            risk = "high" if trend != "falling" and decay >= RISK_DECAY_FLOOR else "low"
            risks.append({
                "risk": risk,
                "pattern": pattern,
                "trend": trend,
                "decay_score": decay,
                "message": (f"Паттерн '{pattern}' встречался в {count} последних сканах "
                            f"(тренд: {trend}, decay: {decay:.1f}). Вероятен повтор."),
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
    matches_by_source = scan_logs_by_source(data)
    # Агрегат (v4/backward compat): сумма по источникам
    matches = {}
    for src, pats in matches_by_source.items():
        for name, count in pats.items():
            matches[name] = matches.get(name, 0) + count

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

    # Добавляем в историю (v5: sources — разбивка по лог-файлам/сервисам)
    data["history"].append({
        "timestamp": timestamp,
        "error_count": sum(matches.values()),
        "patterns": matches,
        "sources": matches_by_source,
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

    # Жизненный цикл learned: обновить активные, вычистить мёртвые (v3)
    refreshed = refresh_learned(data)
    pruned = _prune_stale_learned(data)

    # Тренды и риски персистим в файл — потребители (self_directed_queue)
    # читают ГОТОВЫЙ trend-aware результат, не скатываясь в наивный
    # streak-логик (фикс: очередь всё ещё плодила 8 одинаковых задач
    # "streak: 7", хотя predict_risks уже умел тренды).
    data["decay_scores"] = _decay_scores(data)  # v6: recency-веса для потребителей
    risks = predict_risks(data)
    data["trends"] = {r["pattern"]: r["trend"] for r in risks if r.get("pattern")}
    data["risks"] = risks

    # Контекст-предсказание (v4): пары со-встречаемостей из истории
    # (текущий скан уже в history — его пара тоже считается), затем
    # прогноз парных паттернов по свежим матчам.
    data["cooccurrences"] = _learn_cooccurrences(data)
    companions = predict_companions(data, matches)
    data["companions"] = companions

    # Контекст по МОДУЛЮ (v5): пары в пределах одного лог-файла/сервиса,
    # прогноз с привязкой к источнику (в каком модуле ждать паттерн).
    data["module_cooccurrences"] = _learn_module_pairs(data)
    module_companions = predict_module_companions(data, matches_by_source)
    data["module_companions"] = module_companions

    data["last_update"] = timestamp
    with open(PATTERNS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {
        "status": "ok",
        "matches": matches,
        "streaks": streaks,
        "decay_scores": data["decay_scores"],
        "risks": risks,
        "companions": companions,
        "module_companions": module_companions,
        "cooccurrence_pairs": sum(len(v) for v in data["cooccurrences"].values()),
        "module_cooccurrence_pairs": sum(len(v) for v in data["module_cooccurrences"].values()),
        "learned_total": len(data.get("learned_patterns", [])),
        "learned_new": added,
        "learned_refreshed": refreshed,
        "learned_pruned": pruned,
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
            dec = f" [decay {r['decay_score']:.1f}]" if "decay_score" in r else ""
            lines.append(f"    {r['risk'].upper()}{trend}{dec}: {r.get('message', '')}")
            if r.get("suggestion"):
                lines.append(f"      → {r['suggestion']}")

    if result["learned_new"]:
        lines.append(f"  🧬 Выучено новых паттернов: {result['learned_new']} (всего {result['learned_total']})")

    if result.get("companions"):
        lines.append("  ⏭ Прогноз по контексту:")
        for c in result["companions"]:
            lines.append(f"    → {c['pattern']} (co-score {c['co_score']})")

    if result.get("module_companions"):
        lines.append("  🎯 Прогноз по модулям:")
        for c in result["module_companions"]:
            lines.append(f"    → {c['pattern']} в {c['source']} (co-score {c['co_score']})")

    if not result["matches"] and not result["risks"] and not result.get("companions") \
            and not result.get("module_companions"):
        lines.append("  ✅ Новых паттернов ошибок не найдено.")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print(json.dumps(update_patterns(), indent=2, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "feedback":
        # CLI обратной связи companion (цикл 28): подтвердить/опровергнуть
        # предсказание руками. Usage:
        #   feedback <pattern> <confirmed> [--module M] [--boost B] [--penalty P]
        # confirmed: true/false/1/0/yes/no. Exit 0 — фидбек применён,
        # exit 1 — невалидные аргументы или паттерн не найден.
        argv = sys.argv[2:]
        if len(argv) < 2:
            print(json.dumps({"error": "usage: feedback <pattern> <confirmed> "
                                       "[--module M] [--boost B] [--penalty P]"},
                             ensure_ascii=False))
            sys.exit(1)
        pattern, confirmed_raw = argv[0], argv[1].strip().lower()
        if confirmed_raw in ("true", "1", "yes", "y"):
            confirmed = True
        elif confirmed_raw in ("false", "0", "no", "n"):
            confirmed = False
        else:
            print(json.dumps({"error": f"invalid confirmed: {argv[1]!r}"},
                             ensure_ascii=False))
            sys.exit(1)
        module, boost, penalty = None, FEEDBACK_BOOST, FEEDBACK_PENALTY
        i = 2
        while i < len(argv):
            if argv[i] == "--module" and i + 1 < len(argv):
                module = argv[i + 1]
                i += 2
            elif argv[i] == "--boost" and i + 1 < len(argv):
                try:
                    boost = float(argv[i + 1])
                except ValueError:
                    print(json.dumps({"error": f"invalid boost: {argv[i + 1]!r}"},
                                     ensure_ascii=False))
                    sys.exit(1)
                i += 2
            elif argv[i] == "--penalty" and i + 1 < len(argv):
                try:
                    penalty = float(argv[i + 1])
                except ValueError:
                    print(json.dumps({"error": f"invalid penalty: {argv[i + 1]!r}"},
                                     ensure_ascii=False))
                    sys.exit(1)
                i += 2
            else:
                print(json.dumps({"error": f"unknown argument: {argv[i]!r}"},
                                 ensure_ascii=False))
                sys.exit(1)
        report = feedback_companion(pattern, confirmed, module=module,
                                    boost=boost, penalty=penalty)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if report.get("error"):
            sys.exit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "feedback-risk":
        # CLI обратной связи рисков (цикл 30): подтвердить/опровергнуть
        # риск руками. Usage:
        #   feedback-risk <pattern> <confirmed> [--penalty N]
        # confirmed: true/false/1/0/yes/no. Exit 0 — фидбек применён,
        # exit 1 — невалидные аргументы или паттерн не найден.
        argv = sys.argv[2:]
        if len(argv) < 2:
            print(json.dumps({"error": "usage: feedback-risk <pattern> "
                                       "<confirmed> [--penalty N]"},
                             ensure_ascii=False))
            sys.exit(1)
        pattern, confirmed_raw = argv[0], argv[1].strip().lower()
        if confirmed_raw in ("true", "1", "yes", "y"):
            confirmed = True
        elif confirmed_raw in ("false", "0", "no", "n"):
            confirmed = False
        else:
            print(json.dumps({"error": f"invalid confirmed: {argv[1]!r}"},
                             ensure_ascii=False))
            sys.exit(1)
        penalty = RISK_FEEDBACK_PENALTY
        i = 2
        while i < len(argv):
            if argv[i] == "--penalty" and i + 1 < len(argv):
                try:
                    penalty = float(argv[i + 1])
                except ValueError:
                    print(json.dumps({"error": f"invalid penalty: {argv[i + 1]!r}"},
                                     ensure_ascii=False))
                    sys.exit(1)
                i += 2
            else:
                print(json.dumps({"error": f"unknown argument: {argv[i]!r}"},
                                 ensure_ascii=False))
                sys.exit(1)
        report = feedback_risk(pattern, confirmed, penalty=penalty)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if report.get("error"):
            sys.exit(1)
    else:
        print(get_report())
