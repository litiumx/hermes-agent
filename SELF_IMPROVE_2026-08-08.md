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
