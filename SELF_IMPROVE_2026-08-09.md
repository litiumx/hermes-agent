# SELF_IMPROVE 2026-08-09 (AGI Coding Cycle 5)

## Цель цикла
agi_curious_agent.py — standalone-тесты полного цикла (кандидат из памяти 08.08:
"rate-limit есть, но нет отдельного теста полного цикла").

## Что сделано (коммит dbbe0a9, push выполнен)
1. **Баг: load_knowledge без нормализации типов.** Валидный JSON не-dict
   (top-level строка/список) или null-поля возвращались как есть →
   `run_research` падал с `TypeError: string indices must be integers` /
   `'NoneType' object is not iterable`. Теперь: не-dict → дефолт, не-list
   findings/topics_searched → [], не-числовой last_search → 0.
2. **Баг: save_knowledge с None-полями** — `len(None)` TypeError. Теперь
   не-list поля молча приводятся к [] перед ротацией.
3. **scripts/agi_test_curious_agent.py — 20 standalone-тестов** полного цикла:
   load_knowledge (missing/битый JSON/JSON-строка/null-поля), save_knowledge
   (ротация 50 findings / 100 topics_searched, None-поля), кулдаун 1ч
   (skip / force / override), полный цикл research (ok, last_search,
   отсутствие дублей при re-research), провал поиска → no_new + retry,
   очистка отравленных записей topics_searched, парсеры DDG
   (html: стрип тегов; lite: // → https; api: Abstract + Related + nested),
   get_active_topics (триггеры fix/error из bridge, дефолт-фолбэк ≤3),
   search_knowledge (тема/snippet/пусто), research_topic структура.

## Проверка
- RED: тесты 2b/3/5 падали с TypeError (баг подтверждён) → GREEN: 20/20
- Регрессия 12/12: config_guard 15, context_store 32, curious_agent 20,
  curious_dedup, directed_topic, error_pattern_learner, focus_agent 26,
  gateway_guard 42, mcp_keepalive 7, queue_cooldown, queue_improvements,
  session_bridge — все PASS
- review: passed (безопасность: только нормализация, без exec/eval/сети;
  тесты изолированы в tempdir с моками web_search_standalone и bridge)

## Push — ВЫПОЛНЕН ✅
ca1912c..dbbe0a9 master -> master.

## Память на потом
- Трюк: в тестах битый JSON писать СЫРЫМ в файл, не через хелпер с
  json.dumps (dumps делает из строки валидный JSON → тест ложно зелёный).
- Кандидаты на след. цикл: agi_code_reviewer.py (ни разу не ревьюился,
  нет тестов), agi_gateway_guard.py cmd_status — проверка gateway_state.json
  битый/протухший.
