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
