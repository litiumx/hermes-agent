# SELF_IMPROVE 2026-08-08 (AGI Coding Cycle)

## Цель цикла
Приоритет #5: focus_agent.py — авто-компрессия (Phase 7). Найдены 2 бага в существующем коде.

## Сделано (коммит [agi] focus_agent, ветка master, репо hermes-agent)
1. **Фикс прунинга в compact_knowledge()** — старая логика считала `pruned_old`
   некорректно (условие в filter ссылалось на исходную длину списка), а записи,
   срезанные финальным капом, вообще не попадали в статистику.
   Теперь: дедуп (deduped) → прунинг старья ТОЛЬКО при превышении лимита (pruned_old)
   → финальный кап (capped) → headroom-сжатие (compressed). Честные счётчики во всех.
2. **Семантика прунинга**: старые записи НЕ удаляются, пока KB меньше лимита —
   они могут быть единственным следом знаний.
3. **Кулдауны в auto_focus_cycle()**: компакция не чаще COMPACT_COOLDOWN_H=6ч
   (action=compact_cooldown вместо спама), советы watch не чаще SUGGEST_COOLDOWN_H=3ч
   (action=watch_cooldown, совет логируется в history). Раньше каждый 30-мин прогон
   при большом контексте дёргал компакцию/совет.
4. **Пустой KB — no-op**: changed=False, history не создаётся.
5. **agi_test_focus_agent.py** — 10 групп тестов (26 asserts): дедуп со склейкой
   источников, no-prune при малом KB, прунинг при лимите, честный capped,
   no-op пустого KB, история с полной статистикой, оба кулдауна, none-режим.

## Проверка
- `python3 -c "compile(...)"` — синтаксис OK
- `python3 agi_test_focus_agent.py` — 26 passed, 0 failed
- Тесты изолированы в tempdir (мокают KB_FILE/HISTORY_FILE/get_context_usage)

## Push
НЕ выполнен: в cron-песочнице нет GITHUB_TOKEN (проверено: `${GITHUB_TOKEN:0:4}` пусто).
Локально репо впереди origin на 13 коммитов. Фикс на хосте (повторно):
`docker_forward_env '["GITHUB_TOKEN"]'` + рестарт gateway — тогда пуши полетят.

## Память на потом
- Приоритеты 1-5 закрыты. Следующие кандидаты: agi_context_store.py (299 строк,
  без тестов), agi_gateway_guard.py, agi_mcp_keepalive.py (нет тестов у обоих).
- Прошлые SELF_IMPROVE: 06.08 (config_guard), 07.08 (session_bridge, curious, queue).

## Цикл 2 — agi_context_store: retention + баг load_context(0) + тесты (коммит ниже)
(кандидат из цикла 1: agi_context_store был 299 строк БЕЗ тестов)

### Что сделано
1. **prune_old() — retention**: sessions обрезается до MAX_SESSIONS=200 последних
   (с удалением ссылающихся снапшотов — FK без ON DELETE CASCADE), снапшоты
   старше SNAPSHOT_TTL_DAYS=7д удаляются. Раньше обе таблицы росли бесконечно
   (каждый save_context = новая строка).
2. **Баг load_context(0)**: `if session_id:` — falsy-0 подменялся «последней
   сессией» (запрос несуществующей сессии 0 возвращал ЛАТЕСТ, а не {}).
   Теперь `is not None` → id=0 честно возвращает {}.
3. **PRAGMA busy_timeout=3000** на каждое соединение — WAL позволяет параллельных
   читателей, но писатели (cron + gateway одновременно) ловили SQLITE_BUSY.
4. **Валидация pending**: save_context/add_pending_task пропускают пустые/None/
   не-строки (раньше `_task_hash(None)` падал бы, пустые задачи копились).
5. **DB_PATH через env AGI_CONTEXT_STORE_DB** — тестируемость без /root-путей.

### Тесты: scripts/agi_test_context_store.py (32 проверки, ALL PASS)
- round-trip save/load (все JSON-поля, ids, latest vs explicit)
- load_context(0) → {} (регрессия на баг #2)
- дедуп pending (case-insensitive), пустые/None/42 пропущены
- add/rm pending: True/False, отсутствующая → False
- TTL: age_out удаляет просроченные, load не возвращает aged
- снапшоты только для complete/interrupted/error, JSON валиден
- prune_old: кап сессий, каскад снапшотов, TTL снапшотов (свежая БД на тест)
- busy_timeout=3000 реально выставлен
- stats + get_report

### Регрессия
session_bridge, error_pattern_learner, queue_improvements, queue_cooldown,
directed_topic, config_guard, curious_dedup, focus_agent — ALL PASS.

### Push
НЕ выполнен: GITHUB_TOKEN в cron-песочнице нет (проверено `${GITHUB_TOKEN:0:4}`
пусто). Локально ~17 коммитов впереди origin/master. Фикс на хосте:
`docker_forward_env '["GITHUB_TOKEN"]'` + рестарт gateway.

## Цикл 3 — mcp_keepalive: фикс сломанного self-test + standalone-тесты (коммит 8d5ece9)

### Проблема
self-test agi_mcp_keepalive.py ПАДАЛ (AssertionError {}): синтетические логи
с хардкод-датами "2026-08-02" — при текущей дате 08.08 все строки отфильтрованы
окном 1ч (ts < cutoff). Тест был сломан дрейфом времени 6 дней.

### Что сделано
1. **Фикс self-test**: `_mk_synthetic_line(server, msg, minutes_ago)` — таймстемпы
   ОТНОСИТЕЛЬНО datetime.now(UTC). Больше не дрейфует. Добавлены кейсы:
   paperclip=crash_loop (3 сбоя/3мин), notebooklm=degraded (3×keepalive, старее 10мин),
   browser=ok (1 сбой), ancient отфильтрован (строка старше окна). 7/7 ассертов.
2. **timedelta в топ-импорт** (был локальный импорт в cmd_self_test — NameError
   при выносе хелпера на уровень модуля).
3. **Новый scripts/agi_test_mcp_keepalive.py — 13 standalone-тестов**:
   _parse_ts (валид/мусор), классификация ok/degraded/down/crash_loop (включая
   границу: 55 сбоев ВНЕ 10-мин всплеска = down, а НЕ crash_loop — приоритеты),
   оконная фильтрация (строки 2ч назад при окне 1ч), типы сбоев (conn/keepalive/tool),
   агрегация одного сервера, save/load_state round-trip + битый JSON + missing
   (STATE_FILE мокается в tempdir), регрессия self-test.
4. **Баги найденные тестами**: приоритет crash_loop над down при всплеске —
   зафиксирован в тесте как ожидаемое поведение (документировано в комменте).

### Проверка
- синтаксис обоих файлов OK
- agi_test_mcp_keepalive.py: 13 passed, 0 failed
- self-test: 7/7 OK
- регрессия 10/10: session_bridge, error_pattern_learner, curious_dedup,
  focus_agent, context_store, config_guard, directed_topic, queue_cooldown,
  queue_improvements, mcp_keepalive + gateway_guard self-test 6/6

### Push — ВЫПОЛНЕН ✅ (впервые за 3 цикла)
GITHUB_TOKEN появился в песочнице (docker_forward_env фикс сработал).
Запушен весь бэклог: origin был на 3fa3f64 → теперь 8d5ece9 (все накопленные
коммиты циклов 1-3 улетели). LOCAL == ORIGIN == 8d5ece9.

### Память на потом
- ОСТОРОЖНО: хардкод-даты в тестах с window-фильтрацией — всегда генерировать
  относительно now (уже 2-й такой баг в AGI-коде).
- Кандидаты на след. цикл: agi_gateway_guard.py (self-test есть, но нет
  standalone-тестов; clean-stale не чистит PID-reuse файлы), agi_curious_agent.py
  (rate-limit сделан, но нет отдельного теста полного цикла).
