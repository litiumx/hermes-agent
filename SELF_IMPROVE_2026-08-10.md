# SELF_IMPROVE 2026-08-10 (AGI Coding Cycle 9)

## Цель цикла
Унификация env-путей в остальных agi_* скриптах (кандидат из памяти цикла 8:
"session_bridge/error_pattern_learner/curious_agent ещё хардкодят
/root/.hermes — кандидаты на унификацию"). Воспроизведено: session_bridge
save_context и curious_agent save_knowledge падали с PermissionError
(/root/.hermes не пишется из песочницы/контейнера), error_pattern_learner
игнорировал env полностью.

## Что сделано (коммит 393dcea, push выполнен)
1. **agi_session_bridge.py**: HERMES_HOME (база) + AGI_SESSION_DIR (точный
   путь) → SESSION_DIR/BRIDGE_FILE/HISTORY_DIR. Дефолты не изменились.
2. **agi_error_pattern_learner.py**: HERMES_HOME + AGI_PATTERNS_FILE /
   AGI_SUPERVISOR_LOG / AGI_SESSION_DIR / AGI_LOG_DIR для 4 путей.
3. **agi_curious_agent.py**: HERMES_HOME + AGI_KNOWLEDGE_FILE / AGI_BRIDGE_FILE.
4. **scripts/agi_test_paths_unified.py — 13 standalone-тестов**: дефолты без
   env (регрессия /root/.hermes), HERMES_HOME → подкаталоги, точные
   оверрайды, живой цикл в tempdir: session_bridge save/load и
   curious_agent save_knowledge (раньше PermissionError). Имена env
   консистентны с циклом 8 (AGI_BRIDGE_FILE/AGI_PATTERNS_FILE/
   AGI_KNOWLEDGE_FILE — те же).

## Проверка
- RED: 8 fail (PermissionError в session_bridge/curious_agent, env
  игнорировался в error_pattern_learner) → GREEN: 13/13
- Регрессия 15/15 наборов: code_reviewer 10, config_guard 15, context_store
  32, curious_agent 20, curious_dedup, directed_topic 6, error_pattern_learner,
  focus_agent 26, gateway_guard 66, mcp_keepalive 7, paths_unified 13,
  queue_cooldown 7, queue_improvements, queue_paths 23, session_bridge — PASS;
  py_compile OK
- Dogfooding: `report` с AGI_*_FILE в tempdir → rc=0, очередь из 2 дефолтных
  задач; _load_bridge_context через session_bridge работает (SQLite-first)
- review: passed (безопасность: только os.environ.get в константах, нет
  shell/exec/сети/ключей; тесты изолированы в tempdir через subprocess)

## Push — ВЫПОЛНЕН ✅
860cee6..393dcea master -> master.

## Память на потом
- Паттерн env-путей теперь единый для 4 скриптов (queue, session_bridge,
  error_pattern_learner, curious_agent). Остались хардкоды: agi_focus_agent.py
  (HERMES_HOME константа), agi_config_guard.py (ROOT), agi_mcp_keepalive.py
  (LOGS_DIR/STATE_FILE), agi_context_store.py (SQLite DB path) — кандидаты на
  след. цикл.
- ВАЖНО: при клонировании в песочнице проверять рабочую копию
  /home/sandbox/hermes-agent — она содержит актуальный master (циклы 8+);
  свежий git clone может отставать, если крон-цикл уже отработал.
- След. кандидаты: унификация focus_agent/config_guard/mcp_keepalive/
  context_store, dogfooding gateway_guard status в proactive_scan.py.
