# SELF_IMPROVE 2026-08-19 — Suggestion в self_directed_queue (цикл 44)

## Реализовано: планировщик потребляет suggestion из predict_risks
**Grow point:** SELF_IMPROVE 18.08 (цикл 43): «self_directed_queue:
потреблять suggestion из predict_risks при создании fix-задач (сейчас
рекомендация только в отчёте)».

**Проблема:** predict_risks писал `suggestion` в каждый риск (в т.ч.
TWO-STRIKE RULE для `tool_call_loop:<tool>`), но self_directed_queue
выбрасывал это поле при чтении PATTERNS_FILE — задача «Investigate and
fix pattern: X» уходила исполнителю БЕЗ совета, рекомендация существовала
только в отчёте learner'а.

**Что сделано (scripts/agi_self_directed_queue.py):**
- `_clean_suggestion(value)` — нормализация: строка с контентом → strip,
  иначе None (пустые/мусорные значения в задачи не попадают).
- `load_state()` — риск несёт `suggestion` из predict_risks.
- `build_queue()` — задача source="risk" получает поле `suggestion`;
  потребитель (run_next/отчёт) видит совет, а не только текст задачи.
- `run_next()` — suggestion едет в результате исполнения (кто вызывает
  run_next, получает рекомендацию вместе со статусом/выводом).
- `get_report()` — под fix-задачами выводится «↳ совет: …» (до 100 симв).

**Тесты:** scripts/agi_test_queue_suggestion.py (8 проверок): suggestion
до задачи очереди, через load_state, риск без suggestion → None, старый
формат файла (без risks) не ломается, run_next возвращает suggestion,
get_report с рисками, пустые/невалидные значения (None/""/"   "/123/list)
→ None, регрессия всех источников (pending/risk/companion).

**Регрессия:** exit-code прогон 52 файлов: 40 PASS, 12 FAIL — ВСЕ 12
идентичны на чистом baseline (git stash): 9 из них падают из-за живого
SQLite-bridge в песочнице (тесты пишут TMP-файлы, но load_context читает
реальный /root/.hermes/session bridge с остатками прошлых прогонов),
3 — scan_* требуют pytest (в песочнице не установлен). Мои изменения:
0 новых падений. review: passed.

## Урок
Тест на планировщик обязан быть hermetic: `q._USE_BRIDGE = False` +
TMP-файлы. Без этого тест-ран падает не из-за кода, а из-за живого
SQLite-bridge в песочнице (остатки pending_tasks от session_bridge-тестов).

## Grow points (следующие циклы)
- focus_agent: при повторном срабатывании detect_repeated_calls после
  compact — эскалация пользователю (не только совет) [цикл 43]
- Параметризовать окно/порог loop-детектора через env [цикл 41]
- Продуктивизация: прогнать полную петлю learner → queue → run_next на
  живом state.db в песочнице (loop_feedback реально доезжает до задачи)


---

# SELF_IMPROVE 2026-08-19 — Эскалация при повторах после компакции (цикл 45)

## Реализовано: repeat_escalated вместо тихого repeat_cooldown
**Grow point:** SELF_IMPROVE 19.08 (цикл 43/44): «focus_agent: при
повторном срабатывании detect_repeated_calls после compact — эскалация
пользователю (не только совет)».

**Проблема:** повторы tool calls детектятся, компакция в кулдауне (была
<6ч назад) → цикл возвращал тихий `repeat_cooldown` с советом «пропускаю».
Если компакция НЕ сломала зацикливание, сигнал терялся — пользователь не
узнавал, что агент застрял.

**Что сделано (scripts/agi_focus_agent.py):**
- `ESCALATION_COOLDOWN_H = 24` — эскалация не чаще раза в сутки (крон
  каждые 30 мин не спамит).
- `escalate_loop(repeats, top_hit=None)` — пишет escalation-событие в
  history (tool, count, message «компакция не сломала зацикливание, нужна
  ручная проверка/остановка»); пустые repeats → тихий отказ без записи;
  битые записи (нет tool) не роняют цикл.
- `auto_focus_cycle()`: повторы + свежая компакция → `action=repeat_escalated`
  с `result["escalation"]`; при свежей эскалации (<24ч) → `repeat_cooldown`
  с пометкой «эскалация была <24ч назад — пропускаю».

**Тесты:** scripts/agi_test_focus_escalation.py (21 проверка): escalate_loop
пишет событие/тихо отказывает на пустом/использует top_hit/устойчив к битым
данным; auto_focus_cycle: эскалация при свежей компакции, кулдаун 24ч не
спамит, старая эскалация (25ч) срабатывает снова, без свежей компакции —
компакция а не эскалация. agi_test_focus_repeats.py: тест 13 обновлён под
новую семантику (27 проверок).

**Регрессия:** exit-code прогон 53 файлов: 41 PASS, 12 FAIL — все 12
идентичны на чистом baseline (git stash): 9 — живой SQLite-bridge в
песочнице, 3 — scan_* требуют pytest. Мои изменения: 0 новых падений.
review: passed.

## Урок
Эскалация — отдельный тип события в history с собственным кулдауном:
совет (suggestion) и эскалация (escalation) — разные уровни серьёзности,
их кулдауны должны быть разными (3ч vs 24ч).

## Grow points (следующие циклы)
- focus_agent: connected escalation → в Telegram-отчёт (сейчас событие
  только в history + result)
- Параметризовать окно/порог loop-детектора через env [цикл 41]
- Продуктивизация: прогнать полную петлю learner → queue → run_next на
  живом state.db в песочнице (loop_feedback реально доезжает до задачи)


---

# SELF_IMPROVE 2026-08-19 — Loop-детектор параметризован через env (цикл 46)

## Реализовано: AGI_REPEAT_WINDOW_SEC / AGI_REPEAT_MIN / AGI_REPEAT_TOP
**Grow point:** SELF_IMPROVE 14.08 (цикл 41, оставался открытым): «Параметри-
зовать окно/порог loop-детектора через env».

**Проблема:** REPEAT_WINDOW_SEC/REPEAT_MIN/REPEAT_TOP захардкожены в
agi_focus_agent.py — перенастройка чувствительности детектора повторов
(окно 2ч, порог 3, топ 5) требовала правки кода и нового деплоя.

**Что сделано (scripts/agi_focus_agent.py):**
- `_env_int(name, default)` — безопасный парсинг int из env: пустое/
  не-число → default, исключений не кидает.
- `detect_repeated_calls()` — параметры читаются из env при КАЖДОМ вызове,
  если аргумент не передан явно (крон перенастраивает без правки кода);
  явные аргументы всегда имеют приоритет над env (backward-compat:
  существующие тесты с явными window_sec/min_repeats/top не затронуты).
- Клэмпы от дегенеративных значений: window_sec ≥ 0, min_repeats ≥ 1,
  top ≥ 1 (env=0/-5/«abc» не роняют и не спамят детектор).

**Тесты:** scripts/agi_test_focus_env_params.py (19 проверок): окно через env
(сужает/расширяет), порог (3 vs 4 вызова), топ (1 vs 2 сигнатуры), приоритет
явных аргументов над env, мусор в env (abc/пусто/x → дефолты), клэмпы
(0 → 1, -5 → 0 без краха), полный auto_focus_cycle() читает env
(env min=4 → нет повторов/action=none, min=3 → детект/repeat-ветка).

**Регрессия:** exit-code прогон 55 файлов: 43 PASS, 12 FAIL — все 12
идентичны на чистом baseline (git stash): 9 — живой SQLite-bridge в
песочнице, 3 — scan_* требуют pytest. Мои изменения: 0 новых падений.
review: passed.

## Урок
Env-параметризация детекторов — дёшево и безопасно, если читать env при
каждом вызове (а не на import): тесты переключают поведение без ре-импорта,
а явные аргументы сохраняют backward-compat. Клэмпы обязательны: оператор
может выставить 0/-1 и сломать порог «повтора» до шума.

## Grow points (следующие циклы)
- focus_agent: connected escalation → в Telegram-отчёт (сейчас событие
  только в history + result) [цикл 45]
- Продуктивизация: прогнать полную петлю learner → queue → run_next на
  живом state.db в песочнице (loop_feedback реально доезжает до задачи)
- Cooldown'ы (COMPACT/SUGGEST/ESCALATION) тоже перевести на env — единый
  механизм настройки частоты циклов без правки кода
