# SELF_IMPROVE 2026-08-18 — Repeated Tool-Call Detector (цикл 41)

## Реализовано: focus_agent — детект повторяющихся tool calls (SELF_IMPROVE_2026-08-14 #2)

Проблема: research 14.08 — на ~80K токенов в multi-step прогоне начинаются
повторяющиеся tool calls (контекст деградирует), а наш auto-compact срабатывал
только на 70% окна (700K) — слишком поздно. Ранний сигнал не использовался.

**Решение** в scripts/agi_focus_agent.py:
- `detect_repeated_calls(db_path=None, window_sec=7200, min_repeats=3, top=5)` —
  сканирует state.db (таблица messages, колонка tool_calls JSON): одинаковые
  сигнатуры (tool + аргументы с sort_keys-нормализацией) за окно 2ч, топ по
  count. Тихий отказ на всё: нет БД/таблицы/битый JSON/битый ts → [].
- `_parse_ts(value)` — created_at в epoch: float И ISO (prod session_logger
  пишет %Y-%m-%dT%H:%M:%SZ) — без этого детект умер бы на реальной схеме.
- `auto_focus_cycle()`: повторы → компакция/совет ПЕРВЫМ делом (до токен-
  порога), action repeat_advised / repeat_cooldown (кулдаун COMPACT_COOLDOWN_H
  общий с основной компакцией). result["repeated_calls"] — для отчёта.

Тесты: agi_test_focus_repeats.py (25) — нет БД/пустая/одиночный, детект 3х,
разные аргументы, нормализация порядка ключей, вне окна, min_repeats граница,
битый JSON, не-assistant роли, битые ts, ISO ts (prod-формат), integration
(повторы → repeat_advised при малом контексте, кулдаун → repeat_cooldown,
без повторов → обычная логика). Регрессия: 49/49 файлов. review: passed.

## Урок
- Продуктивизация детектора: писать парсер под РЕАЛЬНУЮ схему потребителя
  (session_logger пишет ISO, а не float) — иначе фича зелёная в тестах на
  temp-БД и мёртвая в проде. Тест на prod-формат ts обязателен.
- Тест «битый JSON + 1 валидный вызов» с ожиданием «детект» — ошибка теста:
  min_repeats=3 не достигается. Контракт: мусор пропускается, но порог честный.

## Grow points (следующие циклы)
- Запустить детектор в проде и проверить срабатывание на реальных повторах
- Error-pattern интерграция: повторы tool calls → фидбек в learner
  (подтверждённый паттерн «зацикливание»)
- Параметризовать окно/порог через env (сейчас константы модуля)
