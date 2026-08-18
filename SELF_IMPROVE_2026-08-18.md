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

---

## SELF_IMPROVE 2026-08-18 (цикл 42) — Loop Feedback: повторы tool calls → error-pattern learner

## Реализовано: полная петля «зацикливание → риск» (grow point цикла 41)

Проблема: детектор повторов (цикл 41) находил зацикливание tool calls, но
learner об этом не знал — predict_risks предсказывал только текстовые
паттерны из логов. Подтверждённая деградация контекста не давала фидбека
в систему предсказания ошибок.

**Решение:**
- `agi_error_pattern_learner.feedback_loop_evidence(repeats, data=None)`:
  каждый эпизод зацикливания усиливает streak `tool_call_loop:<tool>` и
  пишет журнал feedback type="loop" (кап FEEDBACK_JOURNAL_MAX) + запись в
  history (кап 100, source=tool_call_loop_detector). Без history-записи
  decay_score=0 → риск всегда low — фича была бы мёртвой (урок ниже).
  Пусто/мусор → no-op, saved=False, файл не создаётся.
- `agi_focus_agent.report_repeats_to_learner(repeats, learner_name=...)`:
  ленивый importlib-импорт learner — модуль недоступен → тихий отказ
  {"error": ...}, focus-цикл не падает. Вызов из auto_focus_cycle при
  обнаруженных повторах → result["loop_feedback"] (даже в кулдауне
  компакции: фидбек не спам, а факт).

Полная петля: 3 эпизода зацикливания (streak>=3) + свежие history-записи
→ predict_risks возвращает tool_call_loop:<tool> как HIGH (trend не falling,
decay >= floor).

Тесты: agi_test_loop_feedback.py (25): streak-создание/аккумуляция,
мульти-тулы, пусто/мусор/None, журнал loop с tool/count, капы журнала,
переданный data, реальная доставка через focus, тихие отказы (нет модуля,
нет функции), integration auto_focus_cycle → loop_feedback, полная петля
до predict_risks high. Регрессия: 50/50 файлов. review: passed.

## Урок
- Полупетля = мёртвая фича: записал streak, но не history → decay_score=0
  → риск вечно low. Проверять фичу ПОТРЕБИТЕЛЕМ (predict_risks), а не
  только своим контрактом. Добавил тест 13 «3 эпизода → high» именно
  потому, что первый прогон без history-записи давал бы low.

## Grow points (следующие циклы)
- Продуктивизация: проверить loop_feedback в реальном auto_focus_cycle
  (песочница) на живом state.db
- Suggestion для tool_call_loop:<tool> в _SUGGESTIONS (сейчас fallback
  «Проверить соответствующие сервисы» — можно точечно: «сменить подход,
  TWO-STRIKE RULE»)
- Параметризовать окно/порог детектора через env (grow point цикла 41)


---

## Цикл 43 (18.08): Suggestion для паттернов зацикливания

**Grow point:** SELF_IMPROVE 18.08 (цикл 42): «Suggestion tool_call_loop:<tool> в _SUGGESTIONS».

**Проблема:** predict_risks выдавал для `tool_call_loop:<tool>` generic
«Проверить соответствующие сервисы.» — бесполезно для зацикливания: совет
не говорит СТОП, не упоминает TWO-STRIKE RULE, не называет тул.

**Что сделано (scripts/agi_error_pattern_learner.py):**
- `LOOP_SUGGESTION` — шаблон с TWO-STRIKE RULE и признаком деградации
  контекста (/compact).
- `_suggestion_for(pattern)`: `tool_call_loop:<tool>` → loop-совет с именем
  тула (`.replace` вместо `.format` — имя тула может содержать `{}`);
  известные паттерны → прежний _SUGGESTIONS; неизвестные → generic.
- `predict_risks` использует `_suggestion_for` вместо `_SUGGESTIONS.get`.

**Тесты:** scripts/agi_test_loop_suggestion.py (21 проверка): loop-совет с
TWO-STRIKE + тулом, пустой тул → '?', необычные имена (пробелы, `{}`),
префикс без двоеточия → generic, известные паттерны → совет не сломан,
streak<3 → нет риска, интеграция predict_risks (3 эпизода → high + совет).
Регрессия: 51/51 файлов, 0 падений. review: passed.

**Grow points (дальше):**
- self_directed_queue: потреблять suggestion из predict_risks при создании
  fix-задач (сейчас рекомендация только в отчёте).
- focus_agent: при повторном срабатывании detect_repeated_calls после
  compact — эскалация пользователю (не только совет).
