# AGI Self-Improvement — 2026-08-01

## Создан run-next: agi_self_directed_queue.py теперь ИСПОЛНЯЕТ задачи
- **Проблема**: очередь строилась, но никто её не выполнял — интеграция с cron отсутствовала (grep по proactive_scan/session_bridge/curious_agent пуст)
- **Фикс**: добавлен автономный раннер — `run_next()` + CLI `run-next`:
  - `TASK_ACTIONS` — маппинг подстрок задачи → команда (proactive scan, self_improve, curious agent, pattern)
  - Выполнение через subprocess с таймаутом 120с, захват exit_code + output_tail
  - История исполнения (20 записей) в data/task_queue.json — статус done/failed/timeout/skipped
  - Кулдаун 6ч для дефолтных задач — не повторяются каждый цикл
- **Тест**: `run-next` выполнил proactive_scan (exit 0), задача удалена из очереди, история записана
- **Коммит**: `[agi] self_directed_queue: run-next runner + история исполнения + кулдаун дефолтов`

## Исправлен: agi_session_bridge.py — шум в диффе от ключей SQLite
- **Проблема**: SQLite `load_context()` отдавал `id/swarm_size/last_known_good` (int 0|1), а JSON-путь — `current_swarm_size/last_known_good_state` (bool) → дифф сравнивал разные наборы ключей и шумел на КАЖДОМ сохранении
- **Фикс**: добавлен `_canonicalize()` — единый канонический вид из любого источника (swarm_size→current_swarm_size, last_known_good int→bool, id не попадает в дифф); `load_context()` возвращает канонический контекст; `_DIFF_IGNORE={"timestamp"}` убран из сравнения
- **Тест**: повторное сохранение → `no changes (SQLite)` (раньше невозможно); смена задачи → в диффе только `last_task: task A → task B`
- **Коммит**: `[agi] session_bridge: canonicalize keys — clean diff (no id/swarm_size noise)`

## Исправлен: agi_session_bridge.py — баг диффа в SQLite-пути
- **Проблема**: `save_context()` в SQLite-режиме вызывал `load_context()` ПОСЛЕ `_sql_save()` — загружалась только что записанная строка, diff всегда был пустым ("no changes (SQLite)")
- **Фикс**: `prev = load_context()` перенесён ДО записи, сравнение через `_normalize_context()`
- **Тест**: SYNTAX OK; функциональный — второе сохранение вернуло `last_task: task A → task B` (раньше: пусто)
- **Коммит**: `[agi] session_bridge: fix SQLite diff — load prev BEFORE save`

## Состояние приоритетов (все выполнены ранее):
1. ✅ session_bridge.py — SQLite-хранение + фикс диффа (сегодня)
2. ✅ error_pattern_learner.py — предсказатель ошибок
3. ✅ curious_agent.py — фоновый исследователь
4. ✅ self_directed_queue.py — автономный планировщик

### Следующие приоритеты:
1. Убрать шум в диффе: нормализовать ключи SQLite (id/swarm_size/current_swarm_size) для чистого сравнения
2. Интеграция self_directed_queue с cron для автономного выполнения задач

## Octopus Research 2026-08-01 — применимые находки
- **CodeAct подтверждён**: execute_code в Hermes = CodeAct-паттерн (MAF Build 2026). Для цепочек из 3+ тулзов с обработкой между ними — использовать execute_code, а не последовательные вызовы (меньше turns, токенов, латентности)
- **Memory benchmarks (LoCoMo/LongMemEval/BEAM)**: память Hermes (SQLite+zvec+FTS5) — кандидат на прогон LongMemEval-сценария. Открытые проблемы индустрии: cross-session identity, temporal abstraction, staleness — учитывать при развитии memory tool
- **Active Context Compression (arXiv 2601.07190)**: Focus Agent прунит сырую историю в Knowledge block. Hermes уже сжимает при 70% — идея: проверять failure-driven оптимизацию (анализ сбоев после сжатия)
- **NotebookLM auth stale** — нужен re-auth (n8n MCP работает, новостной workflow ок)
