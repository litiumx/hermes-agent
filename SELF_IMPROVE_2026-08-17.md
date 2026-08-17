# SELF_IMPROVE 2026-08-17 — Temporal Supersession (цикл 37)

## Реализовано: memory supersession (SELF_IMPROVE_2026-08-16 #2, MemClaw)
Проблема: store_memory при перезаписи value МОЛЧА уничтожал старый факт —
provenance collapse (инцидент MemGhost). Старую версию нельзя было восстановить.

**Решение** в agi_context_store.py:
- Новая append-only таблица `memory_history` (key, old_value, old_tier, new_value, superseded_at) — строки никогда не редактируются
- store_memory: при value-изменении фиксирует superseded-версию ДО update (тир-апгрейд и одинаковый value — без записи)
- `get_memory_history(key=None, limit=50)` — newest-first, переживает eviction из memory_items (retain_memory)
- `memory_history_stats()` → {total, keys}; get_report показывает секцию "Superseded версий"
- Миграция: CREATE TABLE IF NOT EXISTS в _ensure_db — старые БД обновляются автоматически

Тесты: agi_test_memory_supersede.py (31) — edge: новый ключ, same value, смена, 4 store=3 supersessions, тир-апгрейд, невалидные входы, limit/фильтр/пусто, eviction-survival, отчёт. Регрессия: 45/45 файлов.

## Осталось (приоритет)
1. **Post-Retrieval Assembly** (arXiv 2606.01435) — явное разделение фаз извлечение→политика→ответ в промпте (частично: Verified Memory CAS)
2. Provenance для memory_history: источник записи (user/email/агент) — колонка source
3. Swarm safety — держать (правила delegate_task)

## Урок
Тесты на общую БД без фильтра по ключу → кросс-контаминация (9 ложных FAIL на первом прогоне). Валидировать тесты изолированно по ключу/фикстуре, не по глобальному счётчику.

## Цикл 38 — provenance: колонка source в memory_history (SELF_IMPROVE #2)
Проблема: superseded-версии хранили ЧТО и КОГДА, но не КТО перезаписал факт —
источник (user/email/агент) терялся, provenance collapse оставался наполовину закрытым.

**Решение** в agi_context_store.py:
- `store_memory(key, value, tier="short", source=None)` — новый параметр; пустой/не-строка → 'agent', пробелы обрезаются
- memory_history + колонка `source TEXT NOT NULL DEFAULT 'agent'`; миграция старых БД через PRAGMA table_info + ALTER TABLE в _ensure_db (обратная совместимость: legacy-строки получают 'agent')
- `get_memory_history` возвращает source; get_report показывает разбивку источников ("источники: agent 2, email 1")

Тесты: agi_test_memory_history_source.py (23) — default/явный/email, невалидные source (None/""/123), strip, несколько источников, API-слой, миграция старой схемы с сохранением строк, stats shape, отчёт. Регрессия: 46/46 файлов.

## Урок
3 ложных FAIL на первом прогоне — не код, а ТЕСТ: путал ASC-порядок raw-чтения (ORDER BY id — старые сверху) и семантику return store_memory (новый ключ=True). Сначала проверять инварианты теста, потом код.

## Цикл 39 — автопосадка honeytoken при пустом сторе (WEEKLY_REVIEW 17.08, план недели #1)
Проблема: если стор приманок пуст (чистка, сбой, удаление), детекция выноса
молча теряет покрытие — никто не заметит, пока не поздно.

**Решение** в scripts/agi_honeytoken.py:
- `ensure_coverage(min_tokens=3, empty_days=7)` — автопосадка по правилу:
  valid >= min → no-op; 0 < valid < min → досадка сразу (дыра не ждёт);
  valid == 0 → empty_since-метка, посадка через empty_days дней (0 = сразу)
- Битые записи (нет marker/planted_at) не в счёт; planted_total не теряет историю
- `_load` теперь сохраняет незнакомые ключи (empty_since не исчезает при save)
- Защита от битой метки: float() в try, мусор = первый вызов
- CLI: `auto-plant [--min N] [--days N]` — точка входа для proactive_scan

Тесты: agi_test_honeytoken_coverage.py (10 групп) — no-op, waiting до N дней,
посадка после N дней, досадка, битые записи, идемпотентность, empty_days=0,
CLI, planted_total. Регрессия: 47/47 файлов. review: passed.

## Урок
Ловушка read_file: русские докстринги в UTF-8 сбивают бинарный детектор
(valid UTF-8, NUL нет, а read_file отказывается) — читать через python3.
Сначала проверить `data.decode('utf-8')`, потом выбирать инструмент.
