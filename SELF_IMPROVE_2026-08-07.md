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

## Цикл 3 — curious_agent: dedup topics_searched (коммит 0520a21)
(приоритет #3; error_pattern_learner закрыт в цикле 2)

### Баг
Directed re-research одной stale-темы (topics_override) ДОБАВЛЯЛ тему в
topics_searched каждый раз, не удаляя старое вхождение → список забивался
дублями (репро: 3 цикла → ['stale X']×4). Кап 100 уходил на одну тему,
отчёт «Тем исследовано: N» врал (считал длину списка, не уникальные темы).

### Фикс
Перед append — удаление старых вхождений темы:
`topics_searched = [t for t in topics_searched if t != topic]`, затем append.
Бонус: `next_topic` теперь один вызов get_active_topics() вместо двух.

### Тесты: scripts/agi_test_curious_dedup.py (5 проверок, ALL PASS)
- 3 directed цикла одной темы → topics_searched без дублей
- новая тема добавляется без потери старых
- регрессия: без override searched-темы пропускаются, дублей нет
- провал поиска → тема НЕ помечается исследованной (retry возможен)
- next_topic присутствует при пустом контексте

### Регрессия
agi_test_session_bridge, agi_test_error_pattern_learner,
agi_test_queue_improvements, agi_test_directed_topic, agi_test_config_guard —
ALL PASS.

### Следующие кандидаты (приоритеты)
1. focus_agent.py — Phase 7 авто-компрессия (не трогали с прошлых циклов)
2. self_directed_queue.py — cooldown для pending-задач из bridge (сейчас
   только risks/defaults кулдаун; pending может плодить повторы между циклами)


## Цикл 4 — self_directed_queue: cooldown для pending/stale-topics (коммит ниже)
(приоритет #4; кандидат из цикла 3)

### Баг
pending-задачи из bridge и directed stale-темы НЕ имели кулдауна (в отличие
от risks и defaults). build_queue() вызывается каждый цикл (30-60 мин), и
пока pending висел в bridge.json (удаляется только mark_completed/run_next),
задача возвращалась каждый раз → дубли в history, спам-прогоны одной задачи.
Аналогично: упавший research stale-темы (search fail) ре-квеился каждый цикл.

### Фикс
Общий кулдаун на уровне очереди: `recent_tasks` (history за последние
DEFAULT_COOLDOWN=6ч) вычисляется ДО секций 1-3 и применяется к:
1. pending из bridge (skip если task in recent_tasks)
2. risks (как было, теперь через общий блок)
3. stale_topics (skip по точному тексту directed-задачи)

Бонус: skipped-задачи (нет action mapping) теперь тоже уходят в кулдаун —
раньше run_next() скипал их, но следующий build_queue() возвращал ту же
задачу наверх очереди (спам skipped в history).

### Тесты: scripts/agi_test_queue_cooldown.py (7 проверок, ALL PASS)
- pending с прогоном <6ч не ре-квеится; новая задача попадает
- pending после истечения кулдауна возвращается
- stale-тема с недавним failed research не ре-квеится; generic не дублирует
- stale-тема без кулдауна → directed задача есть
- run_next интеграция: skipped → history → следующий build_queue без задачи
- регрессия: risk-кулдаун работает
- дедуп дублей pending внутри одного bridge

### Регрессия
agi_test_session_bridge, agi_test_error_pattern_learner, agi_test_queue_improvements,
agi_test_directed_topic, agi_test_config_guard, agi_test_curious_dedup — ALL PASS.

### PUSH
По-прежнему НЕ выполнен — GITHUB_TOKEN нет в cron-песочнице. Локально
16 коммитов впереди origin/master. Фикс на хосте: docker_forward_env
'["GITHUB_TOKEN"]' + рестарт gateway.

### Следующие кандидаты
1. focus_agent.py — Phase 7 авто-компрессия (не трогали; проверить работу
   compact_knowledge на реальном knowledge_block.json)
2. agi_code_reviewer.py / agi_gateway_guard.py — не ревьюились ни разу
