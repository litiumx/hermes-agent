# SELF_IMPROVE — 2026-08-11 (AGI Cycle 13)

## Реализовано: append-only audit лог (OptMem-паттерн) в agi_context_store

**Источник**: SELF_IMPROVE_2026-08-10, APPLY #3 (OptMem, 1205★) + grow point
"OptMem merge-логика vs Hermes memory". Приоритет #1 (хранение контекста).

**Суть паттерна**:
- **LOG** — append-only таблица `audit_log`: строки только добавляются,
  НИКОГДА не редактируются. Лог = источник истины.
- **SUMMARY** — таблица `audit_summary`: пересобираемый кэш
  (`rebuild_summary()`), идемпотентный, из лога. Лог не трогается.

**Что добавлено (agi_context_store.py)**:
1. Таблицы `audit_log` (id, ts, op, payload_json) + `audit_summary`
   (id=1, rebuilt_at, total_events, last_id, op_counts_json).
2. `append_audit(op, payload)` — валидация op (str, непустой), не-JSON payload → {},
   возврат id (0 = невалидный op).
3. `get_audit(limit)` — новые первыми; limit<=0 → [].
4. `rebuild_summary()` / `get_audit_summary()` — пересборка из лога, идемпотентна.
5. `audit_integrity()` — append-only инвариант: не-JSON payload в любой строке →
   ok=False; лог вырос после rebuild → stale=True (НЕ коррупция, summary пересобираем).
6. Интеграция с мутациями: save_context → `session_save`, add_pending_task →
   `task_add` (только при реальном добавлении, дубли не логируются),
   remove_pending_task → `task_remove`, age_out_tasks → `tasks_age_out`,
   prune_old → `prune`.
7. `get_stats()` += `audit_events`; CLI += `audit`, `audit-rebuild`, `audit-check`.

**TDD**: 34 теста в agi_test_audit_log.py (RED: AttributeError → GREEN: 34/34).
Изоляция: fresh_db() для тестов с абсолютными счётчиками (общая БД = ложные
FAIL на total/op_counts — урок: absolute counts требуют изолированной БД).

**Регрессия**: 20/20 тест-скриптов PASS (включая session_bridge и context_store).

**Review**: dogfooding code_reviewer на коммите — exfil 0 / persist 0, verdict CLEAN.

## Grow points (завтра)
- Saucedo Multi-Tier Memory Part 2-4 (medium/long-term, KAOS)
- Agent spending-controls vs shopping-протокол (CreditClaw лимиты)
- audit_integrity: добавить сверку last_id в summary против MAX(id) лога
  (сейчас stale считается по total_events — для точности хватит, но last_id
  уже хранится в summary — можно сверять оба)

---

## AGI Coding Cycle 16 (дописано кроном 11.08, репо hermes-agent)

### Цель
Grow point 11.08: «error_pattern_learner v5: контекст по МОДУЛЮ (какой
лог-файл/сервис дал ошибку) — сузить предсказания до сервиса».

### Сделано (коммит 2b996a5, ветка master, push выполнен)
1. **scan_logs_by_source(data)** — матчи ПО ИСТОЧНИКАМ: {errors.log: {pattern:
   count}, gateway.log: ..., supervisor, session}. scan_logs стал агрегатом
   поверх него (backward compat, два пути не расходятся).
2. **История сканов хранит "sources"** — разбивка матчей по лог-файлам.
3. **_learn_module_pairs(data)** — {source: {a: {b: count}}}: пары паттернов
   В ПРЕДЕЛАХ ОДНОГО лог-файла (не «в скане вообще», как v4). Пара (a,b) в
   gateway.log значит «a и b приходили вместе именно в gateway.log».
4. **predict_module_companions(data, current_sources)** — прогноз только по
   парам ТОГО ЖЕ источника: «connection_refused в gateway.log → ждать
   gateway_timeout там же». Пары из других модулей не протекают.
5. **agi_test_error_pattern_module.py** — 23 проверки: разбивка по
   источникам, backward compat агрегата, изоляция кросс-модульных пар,
   порог min_pairs (включая баг {src: {}} после фильтра), legacy-записи,
   исключение присутствующих, пустые входы, лимит+сортировка, интеграция
   через 3 прогона update_patterns.
6. Регрессия: 23/23 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (exfil 0 / persist 0 / danger 0). E2E smoke: update через CLI ок.

### Урок
- Сначала сделал пары глобальными, а источник приписывал «на глаз» — пары
  gateway.log протекали в errors.log (тест поймал: source='errors.log' при
  отсутствии истории там). Правильная модель: карта пар по-модульная
  {source: {a: {b: count}}}, прогноз читает ТОЛЬКО пары своего модуля.
- Фильтр порога в dict-comprehension проверял исходный словарь, а не
  отфильтрованный — оставался {src: {}}. Отдельный цикл с filtered.

### Grow points (следующие циклы)
- curious_agent: вывод устаревших тем по last_researched + score
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- error_pattern_learner v6: ttl/свежесть пар (пары из старых сканов
  взвешивать ниже) или источники в learned-паттернах
