# SELF_IMPROVE — 2026-08-16 (AGI Coding Cycle 33)

## Цель цикла
Grow point из цикла 32: «JSON-fallback для `history` CLI: сейчас JSON-ветка
есть, но не тестируется напрямую». Дополнительно найден разрыв паритета:
SQLite-ветка (get_session_history) фильтрует по окну часов, JSON-ветка
перечисляла ВСЕ снапшоты без фильтра.

## Сделано (коммит 100e31a, ветка master, pushed)
1. **`history_json(hours=24)`** в agi_session_bridge.py — история из JSON-снапшотов
   с паритетом SQLite: фильтр по окну часов (cutoff), сортировка новые→старые.
   Политика legacy: снапшот БЕЗ timestamp включается («возраст неизвестен» —
   та же логика, что в age_out_tasks_json, данные не теряются). Битые снапшоты
   (JSONDecodeError/OSError) пропускаются, остальные целы. Нет каталога → [].
   Возврат: [{timestamp, session_phase, last_task}], ts=0 для legacy.
2. **`_format_history_line(entry)`** — общий CLI-формат `[дд.мм чч:мм] [фаза] задача[:60]`;
   ts=0 → метка `??.?? ??:??` (раньше legacy печатался как эпоха 01.01 03:00).
   Обе CLI-ветки (SQLite и JSON) переведены на него — дубль удалён.
3. **agi_test_session_bridge_history_json.py** — 14 проверок: пустой каталог,
   сортировка/структура записей, фильтр по окну (24ч/100ч с границей 99ч),
   legacy в окне 0ч, битый снапшот, дефолты '?', отсутствие каталога,
   CLI-формат legacy/реального ts.

## Регрессия: 41/41 тест-файлов. review: passed (нет shell-injection/eval/
хардкода; edge cases: пустые входы, битые файлы, legacy, границы окна).

## Замечания по процессу
- Первый прогон тестов упал из-за ОШИБОК В ТЕСТАХ, не в коде: граничные гонки
  с time.time() (снапшот ровно на границе окна) и произвольные имена
  snapshot_*.json, ломающие сортировку (2222... > реальных epoch-ms 1786...).
  Урок: в тестах истории использовать ЗАПАС по времени от границы (≥1ч) и
  реальные epoch-масштабы в именах файлов.

## Следующие кандидаты
- Интеграция age_out_tasks_json + history_json в proactive_scan (очистка задач
  и вывод истории при старте).
- Разделить _DIFF_IGNORE/архивацию: tool_call_count шумит в диффах.
- check_exfil honeytoken на экспортах сессий/email в proactive_scan.

---

# SELF_IMPROVE — 2026-08-16 (AGI Coding Cycle 34)

## Цель цикла
Grow point из цикла 33: «Интеграция age_out_tasks_json + history_json
в proactive_scan (очистка задач и вывод истории при старте)».

## Сделано (коммит a0c6329, ветка master, pushed)
1. **scripts/agi_scan_context.py** — новый модуль-мост для proactive_scan:
   - `cleanup_stale_tasks(max_age_hours)` — возрастная очистка pending-задач
     (обёртка над age_out_tasks_json), возвращает число удалённых;
   - `session_history_lines(hours, limit)` — история сессий из JSON-снапшотов
     (history_json) в CLI-формате, новые→старые, limit обрезает;
   - `context_block(hours, limit)` — готовый блок «📜 Сессии (N ч)» + строка
     «🧹 Очищено устаревших задач: N» только при >0;
   - Всё молча падает в дефолты (try/except) — скан не валится без bridge.
2. **scripts/proactive_scan.py** — при старте печатает context_block()
   (после legacy session summary), тоже в try/except.
3. **scripts/agi_test_scan_context.py** — 8 pytest-проверок: cleanup
   (старые удаляются / legacy без ts сохраняется / пусто и нет файла),
   история (сортировка, фильтр окна с запасом ≥1ч, limit, битые снапшоты,
   legacy ts=0), context_block (полный/пустой, capsys).

## Регрессия: 42/42 тест-файлов (было 41). review: passed (без
subprocess/exec/хардкода; edge cases: пустые входы, битые файлы, legacy,
границы окна).

## Замечания по процессу
- Старые тест-файлы — скриптовый стиль (check/main), НЕ pytest:
  `pytest scripts/agi_test_*.py` их не видит. Регрессия = цикл
  `python3 scripts/agi_test_*.py` по каждому файлу.
- pytest-файлы (новый стиль) тоже исполняемы как скрипты через
  `if __name__ == "__main__": pytest.main([__file__, -v])` — единый цикл работает.

## Следующие кандидаты
- Разделить _DIFF_IGNORE/архивацию: tool_call_count шумит в диффах.
- check_exfil honeytoken на экспортах сессий/email в proactive_scan.
- Дедуп proactive_scan: legacy session_bridge summary + новый context_block
  могут дублировать «последняя задача» — рассмотреть переход на get_session_summary.
