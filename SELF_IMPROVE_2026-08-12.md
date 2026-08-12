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
