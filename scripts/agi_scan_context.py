#!/usr/bin/env python3
"""agi_scan_context.py — контекст сессии для proactive_scan (цикл 34).

Интеграция JSON-fallback session bridge в стартовый скан:
- cleanup_stale_tasks(): возрастная очистка pending-задач (age_out_tasks_json),
  возвращает сколько удалено;
- session_history_lines(): последние сессии из JSON-снапшотов (history_json)
  в CLI-формате, ограниченные limit;
- context_block(): готовый текстовый блок для печати при старте.

Всё молча падает в дефолты при любых ошибках — proactive_scan не должен
валиться из-за недоступности bridge.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agi_session_bridge as sb

DEFAULT_HISTORY_HOURS = 24
DEFAULT_HISTORY_LIMIT = 5


def cleanup_stale_tasks(max_age_hours: float = sb.JSON_TASK_TTL_HOURS) -> int:
    """Удалить устаревшие pending-задачи. Возвращает число удалённых."""
    try:
        return int(sb.age_out_tasks_json(max_age_hours))
    except Exception:
        return 0


def session_history_lines(hours: float = DEFAULT_HISTORY_HOURS,
                          limit: int = DEFAULT_HISTORY_LIMIT) -> list:
    """Строки истории сессий (новые->старые), не более limit."""
    try:
        entries = sb.history_json(hours=hours)
    except Exception:
        return []
    return [sb._format_history_line(e) for e in entries[:limit]]


def context_block(hours: float = DEFAULT_HISTORY_HOURS,
                  limit: int = DEFAULT_HISTORY_LIMIT) -> str:
    """Блок «Контекст сессии»: история + очистка задач. Без исключений."""
    lines = ["📜 Сессии (последние %d ч):" % int(hours)]
    history = session_history_lines(hours=hours, limit=limit)
    if history:
        lines.extend("  " + line for line in history)
    else:
        lines.append("  нет записей")

    removed = cleanup_stale_tasks()
    if removed:
        lines.append(f"🧹 Очищено устаревших задач: {removed}")

    return "\n".join(lines)


def main():
    print(context_block())


if __name__ == "__main__":
    main()
