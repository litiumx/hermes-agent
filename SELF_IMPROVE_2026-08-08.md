# SELF_IMPROVE 2026-08-08 (AGI Coding Cycle)

## Цель цикла
Приоритет #2: error_pattern_learner.py — жизненный цикл learned-паттернов (предсказатель ошибок).

## Проблема
Выученные паттерны (learned_*) были «вечными»: занимали слот MAX_LEARNED=20,
никогда не обновлялись и не удалялись. occurrences застывали на первом
обнаружении, мёртвые регрессии вечно матчились в сканах и шумели в streaks.

## Сделано (v3, локальный коммит, ветка master)
1. **`_collect_error_counts()`** — вынесен подсчёт нормализованных счётчиков
   из learn_new_patterns (общий источник для learn/refresh).
2. **`refresh_learned(data, counts)`** — повторное появление известного
   learned-паттерна теперь увеличивает occurrences и обновляет last_seen.
3. **`_prune_stale_learned(data, max_age_days=14)`** — паттерны без появлений
   14+ дней удаляются (fallback на first_seen для старых записей без last_seen).
4. Новым learned-паттернам проставляется last_seen (= first_seen).
5. `update_patterns()` — вызывает refresh+prune, результат: поля
   `learned_refreshed` / `learned_pruned`.
6. **Тесты 9-11** в agi_test_error_pattern_learner.py: refresh инкрементит
   occurrences (1→3) и обновляет last_seen; prune удаляет stale и legacy-записи
   без last_seen, оставляет свежие; update_patterns отдаёт новые поля.

## Проверка
- `python3 -c "compile(...)"` — SYNTAX OK (оба файла)
- `python3 agi_test_error_pattern_learner.py` — ALL PASS (28 проверок, было 22)

## Push
НЕ выполнен: GITHUB_TOKEN отсутствует в cron-песочнице (проверено
`echo ${GITHUB_TOKEN:0:4}` — пусто). Локально репо впереди origin на 17
коммитов. Фикс прежний: на хосте docker_forward_env '["GITHUB_TOKEN"]'
+ рестарт gateway.

## Память на потом
- error_pattern_learner теперь с TTL-жизненным циклом learned-паттернов
- Следующий приоритет: curious_agent.py (фоновый исследователь) или
  focus_agent.py (авто-компрессия, Phase 7)
