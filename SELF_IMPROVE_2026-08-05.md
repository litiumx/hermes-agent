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
