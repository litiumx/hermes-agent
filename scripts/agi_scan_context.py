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


def _task_from_history_line(line: str) -> str:
    """Задача из строки истории '[дд.мм чч:мм] [фаза] задача'.
    Всё после ВТОРОГО ']' — задача может содержать ']' (урок цикла 40).
    Пустая строка, если формат не распознан."""
    first = line.find("]")
    if first == -1:
        return ""
    second = line.find("]", first + 1)
    if second == -1:
        return ""
    return line[second + 1:].strip()


def dedup_bridge_summary(summary: str,
                         hours: float = DEFAULT_HISTORY_HOURS) -> str:
    """Дедуп bridge-саммари против свежайшей записи истории (цикл 40).

    proactive_scan печатает два блока: «Предыдущая сессия» (bridge) и
    «Сессии (последние N ч)» (история). Свежайший снапшот истории — ЭТО
    предыдущая сессия, поэтому строка «Последняя задача: X» дублируется.

    Убираем ТОЛЬКО строку задачи (она видна в истории), остальную
    информацию bridge (проекты, ошибки, ожидающие) сохраняем.
    При любых ошибках/несовпадениях — саммари без изменений.
    """
    if not summary:
        return summary
    try:
        task = ""
        for line in summary.splitlines():
            if line.strip().startswith("Последняя задача:"):
                task = line.split(":", 1)[1].strip()
                break
        if not task:
            return summary
        history = session_history_lines(hours=hours, limit=1)
        if not history:
            return summary
        hist_task = _task_from_history_line(history[0])
        if not hist_task or task[:60] != hist_task[:60]:
            return summary
        return "\n".join(
            line for line in summary.splitlines()
            if not line.strip().startswith("Последняя задача:")
        ).strip()
    except Exception:
        return summary


def main():
    print(context_block())


if __name__ == "__main__":
    main()
