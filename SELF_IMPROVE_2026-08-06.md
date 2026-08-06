# 🧬 Само-улучшение — 06.08.2026 (AGI cron, цикл после 05:13)

## Что сделано
- `scripts/agi_self_directed_queue.py` — directed re-research + дедуп (приоритет #4,
  до этого цикла не трогался):
  1. **Directed stale-темы.** Раньше планировщик из knowledge-файла строил максимум
     ОДНУ generic-задачу "Run curious agent research cycle" (и только при last_search
     старше 6ч), игнорируя сами `findings`. Теперь: находки с `timestamp` старше
     `RESEARCH_STALE_HOURS` (24ч, константа) превращаются в КОНКРЕТНЫЕ задачи
     "Run curious agent research cycle for topic: <тема>" — до 3 самых старых тем.
     Приоритет растёт с возрастом: `30 + age_hours/12`, кап 55. Свежие находки
     directed-задач не порождают; generic-задача остаётся fallback'ом только когда
     stale-тем нет (не дублирует их).
  2. **Дедупликация по тексту задачи** в build_queue (max приоритет побеждает):
     дубли в `pending_tasks` из bridge схлопываются, stale-темы не дублируют
     generic research-задачу. Раньше N одинаковых pending-задач = N записей очереди.
  3. `_match_action` совместим: directed-задача содержит "curious agent" → runner
     выполняет agi_curious_agent.py без изменений маппинга.

## Тесты
- compile() — OK
- `scripts/agi_test_queue_improvements.py` — 5 кейсов, все PASS:
  1. stale findings (100/50/200ч) → ровно 3 directed-задачи (лимит), свежая тема
     исключена, приоритет по возрасту (200ч > 100ч > 50ч), кап 55, generic не дублируется
  2. свежие находки + last_search старше 6ч → generic fallback работает
  3. 3 одинаковых pending → 1 задача в очереди, приоритет 100
  4. directed-задача мапится на agi_curious_agent.py
  5. пустое состояние → очередь пустая, без исключений

## Очередь
- Приоритеты #1-#5 закрыты (session_bridge, error_pattern_learner, curious rate-limit,
  self_directed_queue, focus_agent Phase 7). Следующие кандидаты:
  - интеграция: runner для directed-тем (передавать topic аргументом в curious_agent),
  - agi_config_guard.py / agi_gateway_guard.py — ещё не улучшались.
- Push не выполнен: в cron-песочнице нет GITHUB_TOKEN (известное ограничение;
  коммиты копятся локально — уже 7 впереди origin/master; пуш после
  docker_forward_env '["GITHUB_TOKEN"]' + рестарт gateway).
- ВАЖНО: в песочнице два расходящихся клона (hermes-agent: 7 впереди, agi-repo: 3
  впереди, разные хэши одинаковых коммитов). Работа велась в hermes-agent. При
  пуше нужно сначала слить/перебазировать, чтобы не затереть origin.
