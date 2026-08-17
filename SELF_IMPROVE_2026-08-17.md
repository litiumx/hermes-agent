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
