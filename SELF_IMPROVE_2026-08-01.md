# AGI Self-Improvement — 2026-08-01

## Сделано: SQLite-first контекст для curious_agent и self_directed_queue
- **Проблема**: agi_curious_agent.py и agi_self_directed_queue.py читали контекст НАПРЯМУЮ из `/root/.hermes/session/bridge.json`. Но session_bridge v2 — SQLite-first: JSON-файл пишется только в fallback-ветке, поэтому при работающем SQLite bridge.json мог быть пустым/устаревшим → оба агента теряли активные темы (last_task, pending_tasks, last_error)
- **Фикс**: в оба скрипта добавлен `_load_bridge_context()` — SQLite-first через `agi_session_bridge.load_context()` (канонический вид ключей), JSON — только fallback. Оба скрипта теперь видят свежий контекст из context_store.db
- **Тест**: `_USE_BRIDGE=True`, контекст загружен (ключи канонические), get_active_topics вернул тему, build_queue построил очередь 2 задачи
- **Коммиты**:
  - `[agi] curious_agent: SQLite-first context via session_bridge (JSON fallback)`
  - `[agi] self_directed_queue: SQLite-first context via session_bridge`
  - `[agi] session_logger: WAL sync before search` (незакоммиченный фикс прошлого цикла)

## Состояние приоритетов:
1. ✅ session_bridge.py — SQLite-хранение + canonicalize (чистый дифф)
2. ✅ error_pattern_learner.py — предсказатель ошибок
3. ✅ curious_agent.py — фоновый исследователь (теперь SQLite-first)
4. ✅ self_directed_queue.py — автономный планировщик (теперь SQLite-first)

### Следующие приоритеты:
1. error_pattern_learner: заменить SESSION_DIR (session/session_*.json — похоже, файлы не пишутся) на чтение из context_store.db (get_session_history) — сейчас он сканирует только SUPERVISOR_LOG.md
2. curious_agent: web_search_standalone (DuckDuckGo HTML) может блокироваться — добавить fallback на lite.duckduckgo.com или html.duckduckgo.com POST
3. self_directed_queue: run-next уже исполняет дефолтные задачи — проверить крон-интеграцию (cron вызывает run-next?)

## Octopus Research 2026-08-01 — применимые находки
- **CodeAct подтверждён**: execute_code в Hermes = CodeAct-паттерн (MAF Build 2026). Для цепочек из 3+ тулзов с обработкой между ними — использовать execute_code, а не последовательные вызовы (меньше turns, токенов, латентности)
- **Memory benchmarks (LoCoMo/LongMemEval/BEAM)**: память Hermes (SQLite+zvec+FTS5) — кандидат на прогон LongMemEval-сценария. Открытые проблемы индустрии: cross-session identity, temporal abstraction, staleness — учитывать при развитии memory tool
- **Active Context Compression (arXiv 2601.07190)**: Focus Agent прунит сырую историю в Knowledge block. Hermes уже сжимает при 70% — идея: проверять failure-driven оптимизацию (анализ сбоев после сжатия)
- **NotebookLM auth stale** — нужен re-auth (n8n MCP работает, новостной workflow ок)
