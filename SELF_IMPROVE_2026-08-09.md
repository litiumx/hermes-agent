# SELF_IMPROVE 2026-08-09 (AGI Coding Cycle 5)

## Цель цикла
agi_curious_agent.py — standalone-тесты полного цикла (кандидат из памяти 08.08:
"rate-limit есть, но нет отдельного теста полного цикла").

## Что сделано (коммит dbbe0a9, push выполнен)
1. **Баг: load_knowledge без нормализации типов.** Валидный JSON не-dict
   (top-level строка/список) или null-поля возвращались как есть →
   `run_research` падал с `TypeError: string indices must be integers` /
   `'NoneType' object is not iterable`. Теперь: не-dict → дефолт, не-list
   findings/topics_searched → [], не-числовой last_search → 0.
2. **Баг: save_knowledge с None-полями** — `len(None)` TypeError. Теперь
   не-list поля молча приводятся к [] перед ротацией.
3. **scripts/agi_test_curious_agent.py — 20 standalone-тестов** полного цикла:
   load_knowledge (missing/битый JSON/JSON-строка/null-поля), save_knowledge
   (ротация 50 findings / 100 topics_searched, None-поля), кулдаун 1ч
   (skip / force / override), полный цикл research (ok, last_search,
   отсутствие дублей при re-research), провал поиска → no_new + retry,
   очистка отравленных записей topics_searched, парсеры DDG
   (html: стрип тегов; lite: // → https; api: Abstract + Related + nested),
   get_active_topics (триггеры fix/error из bridge, дефолт-фолбэк ≤3),
   search_knowledge (тема/snippet/пусто), research_topic структура.

## Проверка
- RED: тесты 2b/3/5 падали с TypeError (баг подтверждён) → GREEN: 20/20
- Регрессия 12/12: config_guard 15, context_store 32, curious_agent 20,
  curious_dedup, directed_topic, error_pattern_learner, focus_agent 26,
  gateway_guard 42, mcp_keepalive 7, queue_cooldown, queue_improvements,
  session_bridge — все PASS
- review: passed (безопасность: только нормализация, без exec/eval/сети;
  тесты изолированы в tempdir с моками web_search_standalone и bridge)

## Push — ВЫПОЛНЕН ✅
ca1912c..dbbe0a9 master -> master.

## Память на потом
- Трюк: в тестах битый JSON писать СЫРЫМ в файл, не через хелпер с
  json.dumps (dumps делает из строки валидный JSON → тест ложно зелёный).
- Кандидаты на след. цикл: agi_code_reviewer.py (ни разу не ревьюился,
  нет тестов), agi_gateway_guard.py cmd_status — проверка gateway_state.json
  битый/протухший.

---

# SELF_IMPROVE 2026-08-09 (AGI Coding Cycle 6)

## Цель цикла
agi_code_reviewer.py — первый полноценный ревью + тесты (кандидат из
памяти цикла 5: "ни разу не ревьюился, нет тестов").

## Что сделано (коммит a210626, push выполнен)
1. **Security-фикс: shell-инъекция через commit_ref.** `run()` и
   `get_diff()` использовали `shell=True` с f-string интерполяцией —
   `commit_ref` из CLI попадал в shell. Теперь list-аргументы без shell;
   тест доказывает: `"ok; touch pwned"` не исполняется.
2. **Фикс `--grep=[agi]`**: в list-форме git трактует `[agi]` как
   regex-класс (26 коммитов вместо 24) → экранирование `\[agi\]`.
3. **Фикс all-режима на shallow-клоне**: `{oldest}~1..{head}` падал
   (у старейшего AGI-коммита нет родителя при --depth 1) → `{oldest}..{head}`.
4. **Env-пути**: AGI_REPO_DIR / AGI_REVIEWS_DIR вместо хардкода
   /root/.hermes — скрипт работает и на хосте, и в песочнице.
5. **scripts/agi_test_code_reviewer.py — 10 standalone-тестов**: run без
   shell, инъекция не исполняется, clean→CLEAN, danger 2→OK/3→WARN,
   syntax→FAIL, статистика+dedupe, пустой diff, save_report JSON,
   get_diff на temp-git-репо (2 коммита), пустой диапазон.

## Проверка
- RED: run(list) падал (старый API) → GREEN: 10/10
- Регрессия 13/13 наборов: code_reviewer 10, config_guard 15, context_store
  32, curious_agent 20, curious_dedup, directed_topic, error_pattern_learner,
  focus_agent 26, gateway_guard 42, mcp_keepalive 7, queue_cooldown 7,
  queue_improvements, session_bridge — все PASS
- Dogfooding: `agi_code_reviewer.py last` → CLEAN на цикле 5;
  `all` → WARN (5 замечаний в полной AGI-истории — корректный сигнал)
- review: passed (безопасность: нет shell/exec/ключей; тесты изолированы
  в tempdir; локальный git, без сети)

## Push — ВЫПОЛНЕН ✅
e2548bc..a210626 master -> master.

## Память на потом
- Shallow-клон: НИКОГДА `~1` для старейшего коммита — только `A..B`.
- git --grep в list-аргументах: экранировать скобки `\[agi\]`, иначе regex-класс.
- Кандидаты на след. цикл: agi_gateway_guard.py cmd_status — проверка
  gateway_state.json битый/протухший (единственный крупный untested блок).


# SELF_IMPROVE 2026-08-09 (AGI Coding Cycle 7)

## Цель цикла
agi_gateway_guard.py cmd_status — проверка gateway_state.json битый/протухший
(кандидат из памяти циклов 5-6: "единственный крупный untested блок").

## Что сделано (коммит 38effbe, push выполнен)
1. **Новая check_state_file(path, label, max_age_h=6)** — полная валидация
   gateway_state.json: отсутствует (норма) / БИТЫЙ JSON / не-объект / нет поля
   gateway_state / STALE (возраст > 6ч по updated_at|ts|timestamp|started_at) /
   состояние error|failed|crash|dead (свежий файл ≠ норма). Намеренный stopped
   и «время неизвестно» — НЕ проблема (defensive, без ложных тревог).
2. **Новая parse_iso_ts(value)** — ISO → datetime UTC: Z-суффикс, числовой
   offset, naive (→UTC), будущий ts (age clamp 0), мусор/None → None.
3. **cmd_status**: раньше gateway_state.json молча печатался INFO (битый файл
   вообще не замечался) → теперь FAIL с причиной + problems++ → exit 1.
4. **Env-тюнинг**: AGI_STATE_MAX_AGE_H переопределяет порог STALE.
5. **+24 standalone-теста** (секции 9-11, итого 66): parse_iso_ts 6,
   check_state_file 14 (missing/broken/list/нет-поля/свежий/stale/ts/started_at/
   error/stopped/без-ts/битый-ts/будущий-ts), cmd_status интеграция 3
   (свежий rc=0, битый rc=1, stale rc=1).

## Проверка
- RED: AttributeError (функций не было), старые 42 зелёные → GREEN: 66/66
- Регрессия 12/12 наборов + self-test 6/6: session_bridge, error_pattern_learner,
  queue_cooldown, queue_improvements, directed_topic, config_guard, curious_dedup,
  focus_agent 26, context_store 32, mcp_keepalive 7, curious_agent 20,
  code_reviewer 10 — все PASS; compile syntax OK
- review: passed (безопасность: нет shell/exec/сети/ключей; тесты в tempdir,
  мок scan_gateway_processes; формат gateway_state.json неизвестен в песочнице —
  спроектировано защитно: 4 ключа таймстемпа, непарсимое время ≠ тревога)

## Push — ВЫПОЛНЕН ✅
bbf5872..38effbe master -> master.

## Память на потом
- Формат gateway_state.json с хоста не виден из песочницы — при первом реальном
  прогоне cmd_status на хосте сверить ключи таймстемпа (updated_at реальный?).
- Приоритеты 1-5 и все известные untested-блоки закрыты. Следующие кандидаты:
  agi_self_directed_queue.py (тесты есть, но планировщик не запускался вживую),
  dogfooding: добавить gateway_guard status в proactive_scan.py.


# SELF_IMPROVE 2026-08-09 (AGI Coding Cycle 8)

## Цель цикла
agi_self_directed_queue.py — планировщик "не запускался вживую" (кандидат из
памяти цикла 7). Воспроизведено: `report` падал с PermissionError — 4 пути
данных и TASK_ACTIONS захардкожены на /root/.hermes (не пишется из песочницы/
контейнера).

## Что сделано (коммит c3f5be8, push выполнен)
1. **Env-пути**: HERMES_HOME (база, дефолт /root/.hermes) + точные оверрайды
   AGI_BRIDGE_FILE / AGI_PATTERNS_FILE / AGI_KNOWLEDGE_FILE / AGI_QUEUE_FILE
   и AGI_SCRIPTS_DIR для TASK_ACTIONS. Дефолты не изменились → хост-поведение
   то же, песочница получила рабочий режим (паттерн цикла 6, code_reviewer).
2. **Pre-check скрипта в run_next**: python3 с отсутствующим файлом возвращал
   exit 2 → задача выглядела "failed", хотя проблема в конфиге. Теперь
   os.path.isfile(cmd[1]) до запуска → статус "error" с reason "script not
   found" (честная диагностика, нет краша).
3. **scripts/agi_test_queue_paths.py — 23 standalone-теста**: дефолты без env
   (регрессия /root/.hermes), AGI_QUEUE_FILE/AGI_SCRIPTS_DIR оверрайды,
   HERMES_HOME → session/data подкаталоги, живой цикл report/next с env
   (раньше PermissionError), run-next end-to-end: done (exit 0), failed
   (exit 1) + кулдаун (второй прогон берёт другую задачу), history записана,
   задача ушла из очереди, отсутствующий скрипт → error с reason.
4. **agi_test_directed_topic.py обновлён**: стаб-скрипты во временной папке
   (run_next теперь pre-check'ает isfile до subprocess.run; тест мокает run и
   проверяет проброс topic-аргумента — интент сохранён).

## Проверка
- RED: 20 fail (PermissionError в 4 путях, run-next не жил) → GREEN: 23/23
- Регрессия 14/14 наборов: code_reviewer 10, config_guard 15, context_store
  32, curious_agent 20, curious_dedup, directed_topic 6, error_pattern_learner,
  focus_agent 26, gateway_guard 66, mcp_keepalive 7, queue_cooldown 7,
  queue_improvements, queue_paths 23, session_bridge — все PASS; py_compile OK
- Dogfooding: `report` с env → rc=0, очередь из 2 дефолтных задач
- review: passed (безопасность: list-args subprocess без shell, env идёт
  только в пути/аргументы, не в exec; тесты изолированы в tempdir, фейковые
  скрипты без сети; ключей нет)

## Push — ВЫПОЛНЕН ✅
28292ec..c3f5be8 master -> master.

## Память на потом
- Паттерн env-путей (HERMES_HOME + AGI_*_FILE + AGI_SCRIPTS_DIR) — единый для
  всех agi_* скриптов; session_bridge/error_pattern_learner/curious_agent
  ещё хардкодят /root/.hermes — кандидаты на унификацию.
- run_next pre-check: python3 с несуществующим файлом = exit 2 (не exception) —
  без явной проверки isfile статус ложно "failed".
- След. кандидаты: dogfooding gateway_guard status в proactive_scan.py (нужен
  доступ к хосту), унификация путей в остальных agi_*.
