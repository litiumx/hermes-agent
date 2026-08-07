# SELF_IMPROVE — 2026-08-07 (AGI cron, цикл 2)

## Цикл 2 — error_pattern_learner: hex-нормализация + import-safety + тесты
(приоритет #2; session_bridge закрыт в цикле 1 того же дня, коммит c70a751)

### Баги, найденные и исправленные
1. **Мёртвый hex-regex**: `re.sub(r"0x[0-9a-f]+", "0xADDR", n, re.IGNORECASE)` —
   4-й позиционный аргумент re.sub это **count**, не flags → IGNORECASE (==2)
   уходил в count, uppercase-адреса (`0xABCDEF01`) не схлопывались. Теперь
   `flags=re.IGNORECASE`.
2. **Порядок нормализации**: `\d+`→N раньше hex-замены убивал паттерн
   (N не hex) — буквенные адреса `0xdeadbeef` оставались уникальными →
   шум в learned-паттернах. Теперь hex сначала, плюс `NxADDR`→`0xADDR`
   restore (маркер содержит цифру 0, которую съедает `\d+`).
3. **Import-safety**: `PATTERNS_FILE.parent.mkdir()` на уровне модуля падал
   PermissionError в cron-песочнице (нет write в /root/.hermes/data) →
   модуль было невозможно импортировать для тестов. Обёрнуто в try/except
   OSError — чтение/--json работают, запись падает явно в update_patterns.
4. **Пропущенный suggestion** для `gateway_timeout` (был в KNOWN_PATTERNS,
   рекомендация уходила в generic fallback).

### Тесты: scripts/agi_test_error_pattern_learner.py (26 проверок, ALL PASS)
- hex: с цифрами / буквенный / mixed-case → 0xADDR (регрессия на баг #1/#2)
- normalize: цифры→N, level+[id]+модуль срезаются, check_*→check_FN
- learn_new_patterns: дедуп по нормализованной строке, повторный вызов → 0
- _pattern_trend: rising/falling/stable/new
- predict_risks: falling→low, rising→high
- scan_logs по tmp-логам (изоляция через monkeypatch глобалов)
- update_patterns: персист trends/risks в tmp PATTERNS_FILE

### Регрессия
agi_test_session_bridge, agi_test_queue_improvements, agi_test_directed_topic,
agi_test_config_guard — ALL PASS.

### Коммит
см. git log. **PUSH НЕ ВЫПОЛНЕН** — GITHUB_TOKEN по-прежнему нет в
cron-песочнице (`${GITHUB_TOKEN:0:4}` пусто). Накоплено 13 локальных коммитов
впереди origin/master. Нужен docker_forward_env '["GITHUB_TOKEN"]' + рестарт
gateway на хосте.

### Следующие кандидаты (приоритеты)
1. curious_agent.py — rate-limit уже добавлен; проверить force/CLI покрытие
2. self_directed_queue.py — dedup задач в JSON-очереди (паритет SQLite)
3. focus_agent.py — Phase 7 авто-компрессия
