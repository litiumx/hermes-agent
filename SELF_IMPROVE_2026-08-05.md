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
