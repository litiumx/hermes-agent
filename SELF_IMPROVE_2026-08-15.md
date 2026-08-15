# SELF_IMPROVE — 2026-08-15 (AGI Coding Cycle 29)

## Цель цикла
Grow point из цикла 28: «retain_memory: вызывать в cron/ежедневном цикле
(сейчас функция есть, автовызова нет) + decay для medium-тира». retain
существовал с цикла 24, но (1) трогал ТОЛЬКО long — нечитаемые medium по
возрасту ПРОМОУТИЛИСЬ consolidate в long (зомби в долгосрочной памяти:
medium_ttl_days=7 в consolidate = возраст, а не доступ); (2) никто его не
вызывал — CLI mem-retain был ручным, ежедневный цикл (auto_focus_cycle)
ретеншн не делал.

## Сделано (коммит 9f70c2b, ветка master)
1. **`retain_memory` + medium decay**: новые параметры `medium_ttl_days=7`,
   `min_medium_accesses=2`. Правило (AND, как у long): medium с
   (last_access=0 ИЛИ last_access < now - medium_ttl_days) И access_count <
   min_medium_accesses → ПОНИЖАЕТСЯ до short (факт НЕ удаляется, но теряет
   консолидированный статус). updated_at сбрасывается на now — повторная
   консолидация только после свежего доступа (нет мгновенного ре-промоута).
   Отключение: medium_ttl_days<=0 или min_medium_accesses<=0.
   Возврат: +`demoted_medium`, audit-событие memory_retain расширено.
2. **CLI `mem-maintain`** в agi_context_store.py: retain → consolidate одной
   командой для крона (порядок важен: decay СНАЧАЛА, иначе consolidate
   промоутит нечитаемые medium в long по возрасту). JSON
   {"retain": {...}, "consolidate": {...}}, exit 0.
3. **Автовызов в `auto_focus_cycle`** (agi_focus_agent.py): раз в сутки
   (MEMORY_MAINTAIN_COOLDOWN_H=24, событие memory_maintain в
   focus_history.json) при `AGI_MAINTAIN_MEMORY=1` (env-флаг — защита от
   случайного трогания prod-БД в тестах/песочнице, где флаг не выставлен).
   Результат в result["memory_maintained"]. Ошибки — тихий отказ (None),
   цикл не роняется.
4. **agi_test_retain_medium_decay.py** — 35 проверок: пустая БД, свежий
   medium выживает, stale+слабый доступ → demote (факт жив в short), AND
   (частый доступ спасает), never-accessed, отключение по ttl/min,
   short не трогается, updated_at сброс (consolidate не ре-промоутит),
   audit, комбо long+medium, CLI mem-maintain (дефолты/decay-off/
   консолидация short→medium).
5. **agi_test_focus_maintain.py** — 10 проверок: флаг off → тишина,
   флаг on + нет истории → maintenance, свежая → кулдаун, старая (50ч) →
   снова, maintenance+компакция вместе.
6. Обновлены 2 старых теста (легитимный контракт-чейндж): test_retain_empty
   (новый ключ demoted_medium в точном равенстве), test_retain_only_long_tier
   (medium теперь демотится, а не «на месте»).
7. Регрессия: 37/37 тестовых файлов PASS. Dogfooding: verdict 🟢 OK
   (danger 0 / exfil 0 / persist 0 / syntax 0, 458 добавлено / 13 удалено,
   review_diff на git diff HEAD + git add -N для untracked тест-файлов,
   AGI_REPO_DIR=/home/sandbox/hermes-agent). Единственное замечание —
   `except Exception` в _memory_maintenance, осознанное (тихий отказ).

## Урок
- Возрастная консолидация БЕЗ decay = фабрика зомби: consolidate промоутит
  medium→long по `updated_at < medium_ttl_days`, поэтому нечитаемый medium
  через 7 дней автоматически становился long. Decay должен идти ПЕРЕД
  consolidate в одном maintenance-шаге, иначе порядок съедает смысл.
- Демоция (tier вниз, факт жив) — правильная семантика для «угасшего»
  medium: long-эвикция удаляет (там факт обязан быть ценным), medium
  деградирует в short и должен ЗАНОВО заработать доступ. updated_at на now
  гарантирует, что consolidate не вернёт его наверх в том же прогоне.
- Env-флаг для автовызова, трогающего prod-БД: фокус-агент в тестах
  ходит в tempdir, но context_store читает env при импорте — без флага
  автономный вызов retain в auto_focus_cycle был бы не-детерминированным
  по тестам и опасным по prod. Флаг + кулдаун + тихий отказ = безопасный
  дефолт.

## Grow points (следующие циклы)
- _pick_gap_topic и _pick_stale_topics: параметр порога для gap-пути
  (сейчас только repeat_hours; stale_hours фиксирован 24ч в
  RESEARCH_STALE_HOURS)
- run_next: авто-фидбек для risk-задач (паттерн в выводе подтверждает
  риск → снижать streak/приоритет, а не только companion)
- short-тир: сейчас decay не трогает short (факт может жить там вечно без
  доступа) — рассмотреть eviction по возрасту для short после стабилизации
  medium-decay
