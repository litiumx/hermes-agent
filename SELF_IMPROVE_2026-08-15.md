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

---

# SELF_IMPROVE — 2026-08-15 (AGI Coding Cycle 30)

## Цель цикла
Grow point из цикла 29: «run_next: авто-фидбек для risk-задач (паттерн
в выводе подтверждает риск → снижать streak/приоритет, а не только
companion)». Петля обратной связи замыкалась ТОЛЬКО для companion-задач
(feedback_companion, циклы 26-28). Risk-задачи («Investigate and fix
pattern: ...», source="risk", генерируются из predict_risks при streak>=3)
исполнялись и удалялись из очереди молча: learner не узнавал, проявился
ли паттерн в выводе расследования. Опровергнутый риск вечно держал
streak на 3+ → задача ре-генерировалась каждый цикл.

## Сделано (коммит f46b7ca, ветка master)
1. **`feedback_risk(pattern, confirmed, penalty=RISK_FEEDBACK_PENALTY=2)`**
   в agi_error_pattern_learner.py: confirmed=True → streak НЕ трогается
   (риск реален, счётчик обновляет сам сканер update_patterns — ручное
   усиление дралось бы с ним); confirmed=False → streak -= penalty
   (floor 0). Одно опровержение сбрасывает streak 3→1 — риск падает ниже
   порога high и перестаёт генерировать задачи. Журнал в data["feedback"]
   с type="risk" (кап FEEDBACK_JOURNAL_MAX), неизвестный/пустой паттерн —
   no-op с error, без журнала. Возврат: streak_before/streak_after/delta.
2. **CLI `feedback-risk <pattern> <confirmed> [--penalty N]`** — ручное
   подтверждение/опровержение риска оператором (JSON, exit 1 на ошибку),
   зеркалит feedback-companion CLI из цикла 28.
3. **run_next авто-фидбек**: risk-элементы очереди теперь несут ключ
   `pattern` (как companion с цикла 26 — без парсинга текста задачи);
   после исполнения паттерн ищется в ПОЛНОМ stdout+stderr → risk_feedback
   в результате. Только source="risk", ошибки learner'а — тихий отказ.
4. **agi_test_risk_feedback.py** — 16 проверок: функция (False → демпфер,
   True → цел, floor, кастомный penalty, пустой/неизвестный паттерн),
   predict_risks-эффект (streak 3→1 → риск исчезает), CLI (8-12), run_next
   (паттерн есть/нет в выводе, не-risk тишина, timeout тишина).
5. Регрессия: 38/38 тестовых файлов PASS (37 старых + 1 новый).
   Dogfooding: verdict ✅ CLEAN (danger 0 / exfil 0 / persist 0 / syntax 0,
   395 добавлено / 1 удалено, review_diff на git diff HEAD + git add -N
   для untracked тест-файла, AGI_REPO_DIR=/home/sandbox/hermes-agent).

## Урок
- Петля предсказаний без фидбека по ИСПОЛНЕНИЮ = вечный генератор задач:
  риск-задача чистила очередь, но не чистила причину (streak), поэтому
  пере-создавалась при каждой сборке. Опровержение должно бить по
  источнику (streak), а не по симптому (очередь).
- confirmed=True для риска — это «не мешать»: streak уже обновляется
  сканером, ручной буст создал бы гонку с update_patterns. Асимметрия
  (True=no-op, False=penalty) — осознанная: демпфер лечит только ложные
  тревоги, подтверждение не подделывает статистику.
- Единый паттерн передачи контекста из очереди в learner (ключ pattern
  в задаче, цикл 26 для companion) переиспользован для risk — не нужен
  парсинг текста задачи, где паттерн может содержать пробелы/скобки.

## Grow points (следующие циклы)
- _pick_gap_topic и _pick_stale_topics: параметр порога для gap-пути
  (сейчас только repeat_hours; stale_hours фиксирован 24ч в
  RESEARCH_STALE_HOURS)
- short-тир: decay не трогает short (факт может жить там вечно без
  доступа) — рассмотреть eviction по возрасту для short после
  стабилизации medium-decay
- risk-фидбек: подтверждённый риск мог бы сбрасывать кулдаун задачи
  (сейчас DEFAULT_COOLDOWN=6ч ждёт даже при подтверждении)

---

# SELF_IMPROVE — 2026-08-15 (AGI Coding Cycle 31)

## Цель цикла
Grow point 15.08 #3 (arXiv 2608.11436): honeytokens в shared memory —
защита от несанкционированного ЧТЕНИЯ/выноса памяти. Verified Memory CAS
защищает запись, но не отвечает «читали ли память». Урок MemGhost
(ложная память через email) показал: вектор «внешний контент → память»
реален; хонейтокены закрывают обратный «память → внешний контент».

## Сделано (коммит 55599a6, ветка master)
1. **agi_honeytoken.py** — приманки-хонейтокены: маркер AGI_HONEY_<8hex>
   в правдоподобном фейковом секрете (note), который легитимный агент
   никогда не использует. plant(n) / check_exfil(text|file) /
   verify() / status() + CLI plant|check|verify|status (exit 1 = утечка).
2. **Детект удаления**: planted_total-счётчик — verify() находит
   удаление записи без следа (removed) и битые записи (missing).
   Legacy-сторы без поля получают planted_total = len(tokens).
3. **agi_test_honeytoken.py** — 10 групп: plant/уникальность/персист,
   повторный plant, note с маркером, check_exfil (1/N маркеров, чистый
   текст, пустой, None, чужой маркер), битый JSON стора, verify
   (removed + corrupt), status, CLI subprocess (exit 0/1/2), check по
   пути файла.
4. Регрессия: 39/39 тестовых файлов PASS. Review: ✅ CLEAN
   (366 строк, good practices: entry point/type hints/docstring).

## Урок
- TDD вскрыл дизайн-пробел: «verify целостности» без счётчика не видит
  удаления — тест на missing после удаления записи упал, потребовался
  planted_total. Счётчик «сколько посажено» проще и честнее томбстоунов.
- agi_code_reviewer в песочнице требует AGI_REPO_DIR=/home/sandbox/hermes-agent
  и AGI_REVIEWS_DIR=/home/sandbox/data/reviews (дефолт /root/.hermes
  недоступен); ревью по хэшу «HEAD» = пустой дифф — нужен "HEAD~1..HEAD".

## Следующие кандидаты
- Интеграция: check_exfil на экспортах сессий/email в proactive_scan.
- Реальный plant на хосте (вне песочницы) для боевого стора.

---

## Цикл 32 (08.08→15.08 вечер, AGI TDD) — session_bridge JSON-fallback parity

**Задача (приоритет #1):** session_bridge — хранение контекста, JSON-fallback отставал от SQLite:
- `age-out` и `stats` CLI падали/сообщали "requires SQLite backend"
- JSON-задачи без таймстампов → age-out в принципе невозможен

**Сделано (TDD: RED→GREEN→REVIEW→COMMIT→PUSH):**
1. Sidecar `_task_created` в JSON-контексте: add_task_json пишет ts, canonicalize прунит ключи без живой задачи (round-trip не теряет, хвостов нет).
2. `age_out_tasks_json(max_age_hours=48)` — паритет с SQLite TTL; legacy-задачи БЕЗ ts не удаляются (возраст неизвестен — не теряем).
3. `get_stats_json()` — задачи/снапшоты/размер bridge (паритет с agi_context_store.get_stats).
4. CLI: `age-out [hours]` и `stats` работают в JSON-режиме.
5. Тесты: 14 проверок в agi_test_session_bridge_json_parity.py (dedup+ts, age-out границы 48h, legacy, stats, rm-task чистит sidecar, round-trip, CLI smoke).

**Регрессия:** 40/40 тест-файлов. **review: passed** (нет shell-injection/eval/хардкода; edge cases покрыты).

**Коммит:** 92936b9, pushed → master (e407035..92936b9).

## Следующие кандидаты
- JSON-fallback для `history` CLI: сейчас JSON-ветка есть, но не тестируется напрямую.
- Разделить _DIFF_IGNORE/архивацию: tool_call_count шумит в диффах (растёт каждое сохранение).
- Интеграция age_out_tasks_json в proactive_scan (очистка задач при старте).
