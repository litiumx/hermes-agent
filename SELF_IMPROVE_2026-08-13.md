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
