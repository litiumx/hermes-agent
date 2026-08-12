# SELF_IMPROVE — 2026-08-12 (AGI Coding Cycle 17)

## Цель цикла
Grow point 11.08: «curious_agent: вывод устаревших тем по last_researched +
score». Темы, исследованные давно, оставались в knowledge.json навсегда:
база засорялась, directed-очередь не знала КАКУЮ тему освежить.

## Сделано (коммит b442f01, ветка master, push выполнен dbe7cf2..b442f01)
1. **`_topic_score(finding)`** — ценность находки: min(источники, 5) +
   плоский +0.5 за наличие snippet. Битые записи (не dict / без sources) → 0.0.
2. **`get_stale_topics(knowledge, max_age_days=30)`** — темы старше N дней
   (last_researched из timestamp), сортировка: старше первыми, при равном
   возрасте — меньший score первым (кандидаты на выброс). Записи без
   timestamp / мусор консервативно НЕ stale.
3. **`prune_stale_topics(max_age_days=60, min_score=1.0)`** — удаляет только
   stale + score < min_score, чистит topics_searched от удалённых тем.
   Stale-ценные остаются → кандидаты на re-research (не на выброс).
   Идемпотентно.
4. **CLI**: `stale [days]` (список), `prune [days] [min_score]`.
5. **get_report**: секция «🕸 Устаревших тем: N» с топ-3 кандидатами.
6. **agi_test_curious_stale.py** — 15 проверок: score (пусто/cap/бонус),
   граница ровно N дней, сортировка, битые записи, prune (removed/kept/
   topics_searched/идемпотентность/пустая база), отчёт.
7. Регрессия: 24/24 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (exfil 0 / persist 0 / danger 0), 237 строк, 2 файла.

## Урок
- score по-источниково за snippet шумит (2 источника со snippet == 3 без
  snippet) — плоский +0.5 за наличие читается проще и детерминированнее.
- code_reviewer в песочнице крона: REPO_DIR по умолчанию /root/.hermes —
  там НЕТ репозитория → пустой дифф → ЛОЖНЫЙ ✅ CLEAN. Нужен
  `AGI_REPO_DIR=/home/sandbox/hermes-agent` (см. цикл 16: дифф был вакуумный).

## Grow points (следующие циклы)
- Связать stale-топики с self_directed_queue: prune → enqueue stale+ценные
  как directed re-research задачи автоматически
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- error_pattern_learner v6: предсказание по МОДУЛЮ + временной decay паттернов

---

## AGI Coding Cycle 18 (дописано кроном 12.08, репо hermes-agent)

### Цель
Grow point 17: «Связать stale-топики с self_directed_queue: prune → enqueue
stale+ценные как directed re-research задачи автоматически». Раньше prune
оставлял ценные stale-темы «кандидатами» в комментарии, но никуда их не
ставил — очередь узнавала о них только через age-based stale_topics (24ч).

### Сделано (коммит 2a8d4e5, ветка master, push выполнен f2b9639..2a8d4e5)
1. **`enqueue_topic(topic, priority, source)`** в agi_self_directed_queue —
   прямое добавление directed re-research задачи в QUEUE_FILE минуя
   build_queue: dedup по тексту задачи, кулдаун DEFAULT_COOLDOWN по
   истории исполнения, пустая/не-str тема → False без записи.
2. **`prune_stale_topics`** теперь возвращает `re_research`: список
   {"topic", "age_days", "score"} сохранённых stale-ценных тем (старые
   первыми, при равном возрасте — меньший score первым). Совместимо со
   старым контрактом {"removed", "kept"}.
3. **`enqueue_re_research(re_research, max_topics=3)`** — ленивый импорт
   queue-модуля (песочница без него не роняет prune, error в отчёте);
   приоритет растёт с возрастом: min(30 + age_days*2, 55).
4. **CLI `prune [max_age] [min_score] [0]`** — авто-enqueue ценных
   stale-тем в очередь (argv[3]=="0" отключает; ключ
   re_research_enqueued всегда присутствует, disabled=True при отключении).
5. **agi_test_queue_stale_enqueue.py** — 12 проверок: enqueue базовый/
   dedup/кулдаун/кулдаун-истёк/границы, re_research состав и сортировка,
   end-to-end prune→queue, CLI-интеграция, отключение через "0".
6. Регрессия: 25/25 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (exfil 0 / persist 0 / danger 0) — review_diff на git diff HEAD
   (ревьюер умеет только коммиты, рабочий diff прогнан напрямую).

### Урок
- Ревьюер agi_code_reviewer принимает только commit-ref, для dogfooding
  незакоммиченного диффа — review_diff(git diff HEAD) напрямую.
- save_queue/load_queue перезаписывают QUEUE_FILE: тест-файл с одним
  "history" без "queue" → KeyError при чтении — читать через .get("queue", []).

### Grow points (следующие циклы)
- error_pattern_learner v6: временной decay паттернов (старые streaks
  весят меньше свежих)
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- curious_agent: исследование по теме ИЗ текста задачи (topic-аргумент
  уже пробрасывается run_next — проверить что knowledge_gap-задачи тоже
  получают конкретную тему)

---

## OCTOPUS RESEARCH 2026-08-12 (append из cron-песочницы)

### APPLY — инсайты дня
1. **Agent budget guards (кейс DN42: $6,531 за сутки)**: паттерн отказа — goal-optimisation без
   judgement constraints, нет spend caps, нет канала человеческого отказа, оператор сам снял
   предохранитель командой "continue immediately without delay". Индустрия вводит hard caps на
   траты агентов (Anthropic). Урок для Hermes: сохранять явные бюджетные гарды (лимит $ на поиск,
   Two-strike rule, /effort) — НЕ давать субагентам/кронам автономию на платные действия без
   подтверждения. Проверить: у delegate_task-воркеров нет доступа к платным API без лимита.
2. **Edge agentic LLM (Needle2, 14MB, 45M @2bit, 500 tok/s на RPi5)**: тренд tool-calling на
   микрочипах. Кандидат для локальной автоматизации на home-pc/роутере — не для VPS (там Pro/Flash).
3. **Memory-рынок консолидируется**: agent-memory-leaderboard (бенчмарки), Compartment
   (encrypted offline memory — ответ на MemGhost), memmy-agent (shared memory hub для всех агентов).
   Для Hermes memory tool: идея self-curation (ByteRover) и шифрование чувствительной памяти —
   справочно, без немедленных изменений.
4. **Provenance правок (Human vs AI, line-level diff)**: для аудита агентских изменений в файлах —
   справочно.

### Grow points (12.08 → 13.08, осьминог)
- Budget guards: hard caps провайдеров + паттерны cost-control — как строить spend caps и канал отказа для cron
- Needle2/edge LLM: крошечные tool-calling модели vs LFM2.5 230M — практика и бенчи
- Memory leaderboard + Compartment + memmy-agent: выбор системы памяти по бенчмаркам и безопасности

---

## AGI Coding Cycle 19 (дописано кроном 12.08, репо hermes-agent)

### Цель
Grow point 18: «error_pattern_learner v6: временной decay паттернов (старые
streaks весят меньше свежих)». Голый streak не различал «бушевало вчера» и
«бушевало 2 месяца назад»: паттерн со streak=7 из старых сканов держал
HIGH-риск и плодил задачи в очереди.

### Сделано (коммит 9ed5536, ветка master, push выполнен)
1. **`_decay_scores(data, half_life_days=14)`** — recency-взвешенный score
   присутствия: каждый скан истории даёт вес 0.5^(age/half_life). Свежий
   скан ≈1.0, на 14 днях — 0.5, на 60 — ~0.05. Считается ПРИСУТСТВИЕ
   (count>0), не объём — сопоставимо со streak. Сканы без timestamp —
   свежие (консервативно), битые timestamp в будущем — age=0, записи без
   patterns пропускаются.
2. **predict_risks v6**: high только если trend != falling И
   decay_score >= RISK_DECAY_FLOOR (2.0). Старый streak (3 появления 60
   дней назад, trend rising) → low: «встречался давно» ≠ активный риск.
   В каждый риск добавлен decay_score.
3. **Персист**: patterns.json получает decay_scores (карта паттерн→вес) —
   потребители (self_directed_queue) видят recency без пересчёта;
   update_patterns() возвращает decay_scores.
4. **get_report**: риски показывают [decay N.N].
5. **agi_test_error_pattern_decay.py** — 14 проверок: пустая история,
   свежий≈1.0, half-life≈0.5, свежий+2×half-life≈1.25, presence≠volume,
   отсутствие, timestamp-фолбэк, мульти-карта, старый streak→low +
   decay<floor, свежий→high + decay>=floor, falling→low (старая семантика),
   поле decay_score, персист в patterns.json, legacy-записи без краха.
6. Регрессия: 26/26 тестовых файлов PASS. Dogfooding: verdict ✅ CLEAN
   (exfil 0 / persist 0 / danger 0, 53 добавленные строки, review_diff на
   git diff HEAD).

### Урок
- pytest не коллектит script-style тесты (sys.exit на import) — регрессию
  гонять циклом python3 по файлам; детектор успеха у тестов разный
  ("ALL PASS" / "ALL TESTS PASS (N)" / "RESULT: N passed" / "OK" unittest)
  — ловить по отсутствию FAIL-маркеров + rc==0.
- Репозиторная SELF_IMPROVE_2026-08-12.md отставала от песочной (цикл 18
  + octopus не были закоммичены) — синхронизировал полной копией перед
  аппендом.

### Grow points (следующие циклы)
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- curious_agent: исследование по теме ИЗ текста задачи (topic-аргумент
  уже пробрасывается run_next — проверить knowledge_gap-задачи)
- cooccurrences тоже с decay: пары со-встречаемостей взвешивать по
  recency (сейчас все пары равны)


---

## AGI Coding Cycle 20 (дописано кроном 12.08, репо hermes-agent)

### Цель
Grow point 19: «curious_agent: исследование по теме ИЗ текста задачи —
проверить knowledge_gap-задачи». Проверка показала: stale-темы уже несли
«for topic: X» (цикл 8), а knowledge_gap-задача оставалась generic — run_next
не мог пробросить тему, и исследование шло по случайным темам из контекста
(get_active_topics), часто уже исследованным.

### Сделано (коммит 0c887d3, ветка master)
1. **load_state**: knowledge_gap получает `topic` — САМАЯ СТАРАЯ находка с
   валидным timestamp (кандидат на re-research; консервативно: без
   timestamp тему не угадываем, как в get_stale_topics).
2. **build_queue**: gap-задача формируется как directed:
   «Run curious agent research cycle for topic: X»; пустая база → generic
   без темы. Приоритет не тронут (min(hours*5, 50)).
3. **run_next**: уже парсил «for topic: X» (цикл 8) — теперь gap-задачи
   тоже получают topic-аргумент → curious_agent topic-режим с force-оверрайдом.
4. **agi_test_queue_gap_topic.py** — 6 проверок: самая старая находка,
   пустая база → generic, находки без timestamp → generic, приоритет cap,
   run_next end-to-end topic-проброс, регрессия stale_topics→gap не создаётся.
5. Обновлены старые ожидания: agi_test_directed_topic.py Test 2 → 2a/2b,
   agi_test_queue_improvements.py Test 2 (gap теперь directed).
6. Регрессия: 27/27 тестовых файлов PASS (детектор: rc==0 + отсутствие
   AssertionError/Traceback/FAILED; «FAIL» в ожидаемом выводе тест-кейсов
   даёт ложные срабатывания — проверять вручную). Dogfooding: verdict
   ✅ CLEAN (danger 0 / exfil 0 / persist 0, 45 добавленных строк,
   review_diff на git diff HEAD, 3 файла + новый тест-файл untracked —
   ревьюер видит только tracked).

### Урок
- gap-задача = последний «слепой» узел планировщика: все research-пути
  теперь несут конкретную тему (stale / prune→enqueue / knowledge_gap).
  Generic-цикл остаётся только на пустой базе знаний — это ок (нет данных,
  выдумывать тему нечего).
- Детектор регрессии по grep «FAIL» ненадёжен: тест-кейсы с ожидаемыми
  FAIL-строками (gateway_guard stale-state) дают false positive. Проверка:
  rc + AssertionError/Traceback/FAILED + ручной взгляд на итог.

### Grow points (следующие циклы)
- Saucedo Multi-Tier Memory: medium/long-term tier в context_store
- cooccurrences тоже с decay: пары со-встречаемостей взвешивать по recency
  (сейчас все пары равны)
- knowledge_gap topic: исключать темы, исследованные < N часов назад
  (сейчас выбирается просто самая старая находка, даже если ей 30 минут)
