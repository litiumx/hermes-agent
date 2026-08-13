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
