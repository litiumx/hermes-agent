# 🧬 Само-улучшение — 05.08.2026 (AGI cron, второй цикл)

## Что сделано
- `scripts/agi_session_bridge.py` — улучшено хранение контекста (приоритет #1):
  1. **SIGUSR1-хендлер через основной бэкенд.** Раньше писал напрямую в JSON bridge —
     при активном SQLite load_context() читал БД, а сигнал сохранял в JSON → рассинхрон
     и потеря "interrupted"-фазы. Теперь идёт через save_context() → SQLite.
  2. **summary читает историю из SQLite.** Раньше — только JSON-снапшоты HISTORY_DIR,
     при SQLite-бэкенде секция истории была пустой. Теперь SQLite-first + JSON fallback.
  3. **Убран дубль импорта**: importlib + `_store_call()` вместо 7 отдельных
     `from agi_context_store import ...` алиасов (Pyright-clean).

## Тесты
- compile() — OK
- backend/stats/summary/history/add-task/rm-task — работают
- SIGUSR1 эмуляция: sessions +1, phase=interrupted — PASS
- Pyright: 0 диагностик

## Очередь
- Приоритет #2 error_pattern_learner / #3 curious_agent / #4 self_directed_queue —
  уже улучшались в прошлых циклах (trend-aware риски, DDG fallback-цепочка).
- Следующий кандидат: curious_agent — добавить rate-limit между поисками (сейчас
  возможен быстрый флуд DDG при множестве топиков).

## Цикл 3 (AGI cron, третий запуск дня)
- `scripts/agi_curious_agent.py` — добавлен rate-limit (приоритет #3, из очереди 05.08):
  1. `SEARCH_DELAY` (3.0s, env `AGI_SEARCH_DELAY`) — пауза между реальными поисками
     тем в run_research; НЕ срабатывает после skip уже исследованных тем.
  2. `FALLBACK_DELAY` (1.0s, env `AGI_FALLBACK_DELAY`) — пауза между источниками
     fallback-цепочки (html→lite→api DDG) при недоступности.
- Тесты: compile OK; мок-поиск 3 темы = 0.8s (мин 0.8s) PASS; повторный запуск
  = 0 поисков PASS; частичный (1 из 3 исследована) = 2 поиска/0.4s PASS;
  fallback при сетевой ошибке = 0.6s (мин 0.6s) PASS.
- Коммит: 20a0df9. PUSH НЕ ВЫПОЛНЕН — нет GITHUB_TOKEN/кредов в песочнице.

## Цикл 4 (AGI cron, четвёртый запуск дня)
- `scripts/agi_focus_agent.py` — Phase 7: РЕАЛЬНАЯ авто-компрессия (приоритет #5):
  1. `get_context_usage()` → теперь возвращает (msgs, tokens): токен-эстимация по
     SUM(LENGTH(content))//4, fallback msgs*250. COUNT(*) ненадёжен (turns-счётчик).
  2. `compact_knowledge(max_entries=100, max_age_days=30)` — детерминированная
     компрессия без LLM: дедуп по topic (свежайшая + склейка источников), прунинг
     записей старше 30 дней при превышении капа, headroom-сжатие контента >2000
     символов (fallback — как есть, headroom в песочнице нет).
  3. `auto_focus_cycle()` — порог по токенам (65% окна 1M) + фолбэк на msgs>600:
     при превышении ВЫПОЛНЯЕТ compact_knowledge() (action=compacted), а не только
     советует. История компрессий пишется в focus_history.json (type=compaction).
  4. Идемпотентность: повторный запуск при неизменных данных = changed:False.
- Тесты: SYNTAX OK; 7/7 PASS — дедуп+прунинг+кап, идемпотентность, headroom-fallback,
  cycle high/watch/none, история пишется.
- Коммит: 8de2627. PUSH НЕ ВЫПОЛНЕН — нет GITHUB_TOKEN/кредов в песочнице
  (накоплено 3 локальных коммита: 20a0df9, b960848, 8de2627).
