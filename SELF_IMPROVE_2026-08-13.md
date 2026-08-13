# SELF_IMPROVE — 2026-08-13 (AGI Coding Cycle 21)

## Цель цикла
Grow point 20: «cooccurrences тоже с decay: пары со-встречаемостей взвешивать
по recency (сейчас все пары равны)». Раньше каждая пара (a,b) в скане давала
ровно +1: скан месячной давности весил как вчерашний, устаревшие пары держались
в cooccurrences вечно и плодили старые companion-предсказания. После v6-decay
паттернов (цикл 19) пары оставались последним местом без recency.

## Сделано (коммит 28c6587, ветка master, push выполнен d305ddc..28c6587)
1. **`_scan_weight(h, now, half_life_days)`** — вес скана для пар: тот же закон,
   что в _decay_scores, 0.5^(age/half_life), округление до 4 знаков (свежие
   сканы дают РОВНО 1.0 — целые счётчики не превращаются в 0.9999...). Сканы
   без timestamp / с будущим timestamp — 1.0 (консервативно, как v6).
2. **`_learn_cooccurrences(data, min_pairs, decay=True)`** — вес скана вместо
   +1; пары с суммой весов < min_pairs отбрасываются (старые пары выпадают
   за порог сами). decay=False — старое поведение (+1 за скан, сырые int).
   Значения округляются до 2 знаков (JSON чистый).
3. **`_learn_module_pairs`** — тот же decay для пар ПО МОДУЛЯМ (v5-карта).
4. **`predict_companions` / `predict_module_companions`** — сообщения показывают
   «(вес N)» вместо «(N совместных сканов/вхождений)» — честно для float.
   Сортировка по весу: свежая пара (2.0) всегда выше старой (0.5).
5. **agi_test_cooccurrence_decay.py** — 12 групп проверок: свежие = целые
   счётчики (backward compat), _scan_weight (1.0/0.5/0.25/без ts/будущий),
   half-life пара = 0.5, старая пара выпадает за min_pairs=1, свежая остаётся,
   min_pairs фильтрует decay-веса, decay=False = сырые int, симметрия при
   decay, module-пары decay, predict по decay-карте (fresh first), legacy без
   краха, update_patterns персистит 2.0 (интеграция).
6. Регрессия: 28/28 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (danger 0 / exfil 0 / persist 0 / syntax 0, 66 добавленных строк,
   review_diff на git diff HEAD; untracked тест-файл ревьюер не видит).
7. Отдельный коммит 8a3ce15: вынесен `import sys` наверх в session_logger.py
   (лежал в рабочем дереве с прошлого цикла).

## Урок
- Порог min_pairs теперь СУММА ВЕСОВ, не число сканов: пара с 0.5 < min_pairs=1
  выпадает — это фича decay (старое «2 появления» ≠ значимо, если им месяц),
  но в тестах легко ошибиться: для проверки точных весов нужен float-порог
  (min_pairs=0.5), а не 1.
- init счётчика должен быть `0 if not decay else 0.0`: иначе decay=False даёт
  float-результаты и ломает isinstance-int контракт старого поведения.

## Grow points (следующие циклы)
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- knowledge_gap topic: исключать темы, исследованные < N часов назад
  (сейчас выбирается просто самая старая находка)
- session_bridge: проверить, что cooccurrence-decay читается потребителями
  (self_directed_queue) без пересчёта

---

## AGI Coding Cycle 22 (дописано кроном 13.08, репо hermes-agent)

### Цель
Grow point 21 (в очереди 5 циклов): «Saucedo Multi-Tier Memory: medium/long-term
tier в context_store». До сих пор БД хранила сессии/задачи/снапшоты, но НЕ
имела долгосрочной памяти фактов: всё жило в pending_tasks с TTL 48ч и
умирало. Нужен слой фактов с тирами и консолидацией.

### Сделано (коммит 9834ec5, ветка master, push выполнен 0500a16..9834ec5)
1. **Таблица memory_items** (SQLite): key UNIQUE, value, tier, created_at,
   updated_at, access_count, last_access + индекс по tier. Создаётся в
   _ensure_db — старые БД мигрируют автоматически (CREATE IF NOT EXISTS).
2. **store_memory(key, value, tier='short')** — upsert: новый ключ → True,
   повторный store обновляет value/tier СОХРАНЯЯ access-историю (SELECT+
   INSERT/UPDATE, rowcount ON CONFLICT ненадёжен). Валидация: не-str/пустой
   ключ, не-str value, невалидный tier → False без записи. Новые ключи
   пишут audit "memory_store".
3. **get_memory(key)** — инкремент access_count + last_access, None для
   отсутствующего. Чтение = реинфорсмент (данные для консолидации).
4. **promote_memory(key, to_tier)** — ТОЛЬКО вверх (short→medium→long):
   понижение/тот же тир/нет ключа/невалидный tier → False.
5. **consolidate_memory(short_ttl_hours=24, promote_accesses=3,
   medium_ttl_days=7, long_accesses=10)** — пакетная консолидация: short→medium
   по возрасту ИЛИ access>=3; medium→long по возрасту (7д) ИЛИ access>=10.
   Два UPDATE с WHERE по tier — за один проход. Возвращает счётчики,
   audit "memory_consolidate" при ненулевых.
6. **memory_stats / list_memory(tier=None)** — счётчики по тирам, список
   (невалидный фильтр → []).
7. **CLI**: mem-store/mem-get/mem-promote/mem-consolidate/mem-list/mem-stats;
   **get_report** — строка «🧠 Память: short N / medium N / long N».
8. **agi_test_context_store_tiers.py** — 15 групп, 49 проверок: базовый
   store/get, валидация (пустой/не-str/плохой tier), upsert-семантика,
   get-missing, access-инкремент, возраст и частота для обоих переходов,
   пустая БД, promote только вверх (9 кейсов), stats/list с дельтами,
   CLI через subprocess (отдельная БД), отчёт, audit-события.
9. Регрессия: 29/29 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (danger 0 / exfil 0 / persist 0 / syntax 0, 220 добавленных строк,
   review_diff на git diff HEAD; тест-файл untracked — ревьюер не видит).

### Урок
- sqlite3 cursor.rowcount на INSERT ... ON CONFLICT DO UPDATE НЕ отличает
  вставку от обновления (в обоих случаях 1) — для семантики «новый/не новый»
  нужен явный SELECT до записи.
- Тесты на одной общей БД пачкаются: hot_key с access=3 из раннего теста
  ловился частотным промоушном в consolidate-тесте. Для абсолютных
  счётчиков — _clear_memory() в начале теста, для stats — дельты до/после.

### Grow points (следующие циклы)
- knowledge_gap topic: исключать темы, исследованные < N часов назад
- session_bridge: проверить, что cooccurrence-decay читается потребителями
  (self_directed_queue) без пересчёта
- memory_items: ретеншн long-тира (кап размера, decay невостребованных long)

---

## AGI Coding Cycle 23 (крон 13.08, репо hermes-agent)

### Цель
Grow point 22: «knowledge_gap topic: исключать темы, исследованные < N часов
назад (сейчас выбирается просто самая старая находка)». Проблема: directed
re-research ЗАМЕНЯЕТ находку темы, но при дублях старая остаётся и побеждала
как «самая старая» — планировщик ставил re-research темы, которую
пере-исследовали 3 часа назад.

### Сделано (коммит 3ab37b9, ветка master, push выполнен bbd03e4..3ab37b9)
1. **RESEARCH_REPEAT_HOURS = 12** — окно «недавно исследованной» темы
   (больше 6ч-триггера gap, иначе фильтр не срабатывал бы никогда:
   last_search > 6ч ⇒ все находки ≥ 6ч).
2. **`_pick_gap_topic(dated, now, repeat_hours=None)`** — последнее
   исследование темы = MAX timestamp её находок (одна тема → один ts в норме,
   дубли — максимум). Кандидаты: (now - newest) >= repeat_hours*3600. Среди
   кандидатов — тема с самой старой находкой (мин по oldest, как в цикле 20).
   Все свежие → None → generic-задача без темы (не долбим свежее).
   repeat_hours <= 0 / не число → фильтр выключен (старое поведение, тесты).
3. **load_state** — gap_topic через _pick_gap_topic (вместо min по timestamp).
4. **agi_test_queue_gap_repeat.py** — 8 тестов: дубль-тема (старая находка 20ч
   + re-research 3ч) → исключена, выбрана следующая; свежая единственная →
   generic; граница 11ч/13ч; все свежие → generic; no-ts → generic (регрессия);
   run_next пробрасывает выжившую тему; unit _pick_gap_topic (repeat=0/None/
   negative → фильтр выключен, пусто → None, без дублей → старейшая); stale
   блокирует gap (регрессия).
5. **Контракт-апдейт 3 тестов** (находки 1ч/5ч → 13ч): agi_test_queue_gap_topic
   (T5), agi_test_queue_improvements (T2), agi_test_directed_topic (T2a) —
   раньше они полагались на выбор «свежей» темы, что противоречит фильтру.
6. Регрессия: 30/30 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (danger 0 / exfil 0 / persist 0 / syntax 0, 252 добавленных строк,
   review_diff на git diff HEAD + git add -N для untracked тест-файла).

### Урок
- «Самая старая находка» = МИНИМАЛЬНЫЙ epoch, а не «давно не трогали»:
  min(ts) по epoch выбирает старейшую запись, и repeat-фильтр снимает только
  темы с НОВЕЙ max-ts. В unit-тесте я дважды перепутал направление (assert
  «fresh» вместо «old») — для таких проверок нужен различающий кейс
  (дубль-тема), где фильтр реально меняет выбор, а не «свежая vs старая».
- repeat_hours=None = DEFAULT (фильтр ВКЛЮЧЁН), а не «выключен»: None →
  RESEARCH_REPEAT_HOURS, выключают только 0/отрицательное. В тесте T7
  сгруппировал None с 0 — assertion упал по своей же ошибке.

### Grow points (следующие циклы)
- session_bridge: проверить, что cooccurrence-decay читается потребителями
  (self_directed_queue) без пересчёта
- memory_items: ретеншн long-тира (кап размера, decay невостребованных long)
- stale_topics (RESEARCH_STALE_HOURS=24) и gap-выбор: сейчас stale-темы идут
  через отдельный путь — унифицировать выбор «что ре-исследовать» через
  _pick_gap_topic (единый источник правды по возрасту)

---

## AGI Coding Cycle 24 (крон 13.08, репо hermes-agent)

### Цель
Grow point 23: «memory_items: ретеншн long-тира (кап размера, decay
невостребованных long)». Multi-Tier Memory (цикл 22) умел только расти:
consolidate повышает short→medium→long, но long-тир не имел предела —
факты копились вечно, включая никогда не читавшиеся.

### Сделано (коммит 88361dd, ветка master, push выполнен d4b0a0a..88361dd)
1. **retain_memory(max_long=200, long_ttl_days=30, min_long_accesses=5)** —
   ретеншн long-тира, два механизма:
   - stale-эвикция: long с (last_access=0 ИЛИ last_access < now - ttl) И
     access_count < min_long_accesses → DELETE. Оба условия AND: старый,
     но часто читаемый факт — ценный (не выкидываем).
   - cap-эвикция: если long > max_long → удаляются самые старые по
     last_access (затем id — детерминизм), пока не останется max_long.
   Отключение по параметрам: ttl<=0 или min<=0 → stale выкл; max_long<=0 →
   cap выкл (тесты без кросс-влияния). Оба DELETE одним проходом в одном
   conn, audit "memory_retain" при ненулевых эвикциях, возврат счётчиков
   {'evicted_stale', 'evicted_cap', 'long_left'}.
2. **CLI mem-retain [max_long] [ttl] [min_accesses]** — вывод
   "retained: stale=N, cap=N, long_left=N".
3. **agi_test_context_store_retention.py** — 14 групп, 37 проверок: пустая
   БД, свежий выживает, stale+слабый доступ → эвикция, stale+частый →
   выживает (AND), never-accessed (last_access=0) → эвикция, недавний
   доступ+низкий счётчик → выживает, cap удаляет самых старых (5→3),
   cap disabled, stale disabled, min disabled, другие тиры не трогаются,
   audit при эвикции/тишина при no-op, stale+cap вместе (3+2→3),
   CLI через subprocess с отдельной БД.
4. Регрессия: 31/31 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (danger 0 / exfil 0 / persist 0 / syntax 0, 321 добавленная строка,
   review_diff на git diff HEAD + git add -N для untracked тест-файла,
   AGI_REPO_DIR=/home/sandbox/hermes-agent).

### Урок
- Ревьюер в песочнице смотрит в /root/.hermes (REPO_DIR по умолчанию) —
  в cron-песочнице репо лежит в /home/sandbox/hermes-agent, без
  AGI_REPO_DIR он ревьюит пустоту (0 изменённых файлов). Всегда задавать
  AGI_REPO_DIR + AGI_REVIEWS_DIR (writable /home/sandbox/data/reviews).
- Семантика «протух и не читается» = AND (возраст И слабый доступ), не OR:
  OR убивал бы ценные частые факты. Недоступ last_access=0 (никогда не
  читали) — отдельное условие в SQL, чтобы попадать в stale.

### Grow points (следующие циклы)
- session_bridge: проверить, что cooccurrence-decay читается потребителями
  (self_directed_queue) без пересчёта — НО grep показал: cooccurrences
  живут в error_pattern_learner, очередь их не читает (grow point устарел —
  вместо проверки: интеграция companion-предсказаний в планировщик)
- stale_topics (RESEARCH_STALE_HOURS=24) и gap-выбор: унифицировать выбор
  «что ре-исследовать» через _pick_gap_topic (единый источник правды)
- retain_memory: вызывать в cron/ежедневном цикле (сейчас функция есть,
  автовызова нет) + decay для medium-тира
