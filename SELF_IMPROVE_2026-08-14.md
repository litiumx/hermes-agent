# SELF_IMPROVE — 2026-08-14 (AGI Coding Cycle 25)

## Цель цикла
Grow point (в очереди с цикла 21): «интеграция companion-предсказаний
в планировщик». error_pattern_learner персистил companions и
module_companions в patterns.json (v4/v5, с decay-весами с цикла 21),
но self_directed_queue их НЕ читал — пре-емптивные задачи для паттернов,
предсказанных «прийти следующими», не создавались. Прогноз был мёртвым
грузом: считался, сохранялся, но не влиял на поведение.

## Сделано (коммит 5115831, ветка master, push выполнен 9469f64..5115831)
1. **COMPANION_MAX_TASKS = 3 / COMPANION_PRIORITY_CAP = 90** — лимит
   companion-задач за сборку и потолок приоритета (риски=100 всегда выше:
   подтверждённая проблема важнее вероятной).
2. **`_companion_priority(co_score)`** — приоритет от веса пары:
   50 + co_score*8, кап 90. co_score=1 → 58, co_score=6 → 90.
3. **`_load_companions(patterns)`** — чтение companions (global, v4) +
   module_companions (v5, с source-модулем) из patterns.json. Дедуп по
   pattern: выживает вариант с МАКСИМАЛЬНЫМ co_score (module 4.0 >
   global 2.0). Malformed (не dict / нет pattern / пустой) пропускаются
   молча. Сортировка по co_score, cap 3.
4. **load_state** — state["companions"] заполняется из patterns.json
   (рядом с рисками; пустой файл/legacy без companions → [] без краха).
5. **build_queue, секция 3** — пре-емптивные fix-задачи:
   - global: "Investigate and fix pattern: X (companion of active errors)"
   - module: "Investigate and fix pattern: X (companion in <source>)"
   - паттерн, уже покрытый риск-задачей, пропускается (не дублируем)
   - кулдаун DEFAULT_COOLDOWN: недавно исполненная задача не возвращается
   - category="fix", source="companion", приоритет из co_score
6. **agi_test_queue_companions.py** — 12 тестов: companions → fix-задача;
   приоритет от co_score (кап 90, сортировка); риск-паттерн не
   дублируется; кулдаун; пустые/отсутствующие companions — тишина;
   malformed (строка вместо списка / без pattern / пустой) — фильтр,
   валидный выживает; module_companions с модулем в тексте; дедуп
   global+module (лучший co_score); cap 3 из 5; run_next исполняет
   companion-задачу (маппинг "pattern"); load_state отдаёт companions;
   legacy patterns.json — без краха.
7. Регрессия: 32/32 тестовых файлов PASS (маркеры разные — "ALL TESTS
   PASS (N)" / "ALL PASS" / "ИТОГ: N passed" — проверка по exit code).
   Dogfooding: verdict ✅ CLEAN (danger 0 / exfil 0 / persist 0 /
   syntax 0, 342 добавленных строк, review_diff на git diff HEAD +
   git add -N для untracked тест-файла, AGI_REPO_DIR=/home/sandbox/hermes-agent).

## Урок
- Прогноз без потребителя — мёртвый груз: learner считал companions
  каждый цикл, но ни один модуль их не использовал. Grow point из
  «проверить, что читается» переформулировался в «интегрировать» —
  при проверке оказалось, что читать некому. Полезный паттерн: после
  добавления новой персистентной структуры сразу искать потребителя.
- Приоритет предсказания должен быть СТРОГО ниже подтверждённого
  факта (риски 100 > companions ≤ 90): иначе очередь забивается
  вероятными проблемами и откладывает реальные.
- Дедуп по pattern между источниками (global vs module) — по max
  co_score, а не «первый встречный»: module-вариант может быть свежее
  и точнее (привязка к источнику).

## Grow points (следующие циклы)
- stale_topics (RESEARCH_STALE_HOURS=24) и gap-выбор: унифицировать
  выбор «что ре-исследовать» через _pick_gap_topic (единый источник
  правды по возрасту)
- retain_memory: вызывать в cron/ежедневном цикле (сейчас функция есть,
  автовызова нет) + decay для medium-тира
- companion-задачи: после исполнения фидбек в learner (подтвердился ли
  companion — усиление/ослабление веса пары)
