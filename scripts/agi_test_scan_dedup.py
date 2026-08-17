#!/usr/bin/env python3
"""agi_test_scan_dedup.py — тесты дедупа proactive_scan (цикл 40).

Проблема (WEEKLY_REVIEW 17.08, grow point): proactive_scan печатает два
пересекающихся блока — «Предыдущая сессия» (session_bridge.get_last_session_
summary) и «Сессии (последние N ч)» (agi_scan_context.context_block).
Свежайший снапшот истории — ЭТО предыдущая сессия: строка «Последняя
задача: X» в bridge-блоке дублирует строку истории «[ts] [phase] X».

Решение: dedup_bridge_summary() — убрать из bridge-саммари строку
«Последняя задача», если она уже видна в свежайшей записи истории.
Остальная информация bridge (проекты, ошибки, ожидающие) сохраняется.

Покрытие:
- дубль: та же задача в истории -> строка задачи удалена, остальное цело
- разные задачи -> саммари без изменений
- истории нет -> без изменений
- пустой саммари / без строки задачи -> без изменений
- legacy-запись (??.?? ??:??) -> дедуп работает
- задача длиннее 60 символов (история режет) -> префикс-сравнение
- "]" внутри задачи -> парсинг истории не ломается
- битая история -> саммари без изменений (безопасный дефолт)
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agi_scan_context as sc
import agi_session_bridge as sb


def _setup(tmp_path):
    """Изолировать пути session bridge в tmp."""
    old = (sb.SESSION_DIR, sb.HISTORY_DIR, sb.BRIDGE_FILE)
    sb.SESSION_DIR = tmp_path
    sb.HISTORY_DIR = tmp_path / "history"
    sb.BRIDGE_FILE = sb.SESSION_DIR / "bridge.json"
    sb.SESSION_DIR.mkdir(exist_ok=True)
    sb.HISTORY_DIR.mkdir(exist_ok=True)
    return old


def _restore(old):
    sb.SESSION_DIR, sb.HISTORY_DIR, sb.BRIDGE_FILE = old


def _snapshot(path, ts, phase="main", task="task"):
    (sb.HISTORY_DIR / path).write_text(json.dumps({
        "timestamp": ts, "session_phase": phase, "last_task": task,
    }))


def _bridge_summary(task="my task", project="proj1"):
    return (f"🧠 Предыдущая сессия:\n"
            f"  Последняя задача: {task}\n"
            f"  Активные проекты: {project}\n"
            f"  🕐 Сессия была: 17.08 12:00")


def test_dedup_removes_duplicate_task_line(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600,
                  "coding", "my task")
        out = sc.dedup_bridge_summary(_bridge_summary("my task"), hours=24)
        assert "Последняя задача" not in out, f"строка задачи осталась: {out}"
        assert "Активные проекты: proj1" in out, f"проекты потеряны: {out}"
        assert "Предыдущая сессия" in out, f"заголовок потерян: {out}"
        assert "Сессия была" in out, f"время потеряно: {out}"
    finally:
        _restore(old)


def test_dedup_keeps_summary_when_tasks_differ(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600,
                  "coding", "other task")
        out = sc.dedup_bridge_summary(_bridge_summary("my task"), hours=24)
        assert out == _bridge_summary("my task"), f"изменено: {out!r}"
    finally:
        _restore(old)


def test_dedup_no_history_unchanged(tmp_path):
    old = _setup(tmp_path)
    try:
        out = sc.dedup_bridge_summary(_bridge_summary("my task"), hours=24)
        assert out == _bridge_summary("my task"), f"изменено: {out!r}"
    finally:
        _restore(old)


def test_dedup_empty_and_no_task_line(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600,
                  "coding", "my task")
        assert sc.dedup_bridge_summary("") == ""
        bare = "🧠 Предыдущая сессия:\n  Активные проекты: proj1"
        assert sc.dedup_bridge_summary(bare, hours=24) == bare
        assert sc.dedup_bridge_summary(None, hours=24) is None
    finally:
        _restore(old)


def test_dedup_legacy_history_entry(tmp_path):
    old = _setup(tmp_path)
    try:
        # legacy-снапшот без timestamp — включается в историю (ts=0 -> ??)
        (sb.HISTORY_DIR / "snapshot_9000000000000.json").write_text(json.dumps({
            "session_phase": "legacy", "last_task": "my task"}))
        out = sc.dedup_bridge_summary(_bridge_summary("my task"), hours=24)
        assert "Последняя задача" not in out, f"legacy-дедуп не сработал: {out}"
        assert "Активные проекты" in out
    finally:
        _restore(old)


def test_dedup_long_task_prefix_match(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        long_task = "очень длинная задача с описанием " * 5  # > 60 символов
        # история режет задачу до 60 символов (_format_history_line)
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600,
                  "coding", long_task[:60])
        out = sc.dedup_bridge_summary(_bridge_summary(long_task), hours=24)
        assert "Последняя задача" not in out, f"префикс-дедуп не сработал: {out}"
    finally:
        _restore(old)


def test_dedup_brackets_inside_task(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        task = "fix [bug] in parser"
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600,
                  "coding", task)
        out = sc.dedup_bridge_summary(_bridge_summary(task), hours=24)
        assert "Последняя задача" not in out, f"] в задаче сломал парсинг: {out}"
        assert "Активные проекты" in out
    finally:
        _restore(old)


def test_dedup_corrupt_history_safe(tmp_path):
    old = _setup(tmp_path)
    try:
        (sb.HISTORY_DIR / "snapshot_5000000000000.json").write_text("{corrupt!!!")
        out = sc.dedup_bridge_summary(_bridge_summary("my task"), hours=24)
        assert out == _bridge_summary("my task"), f"битая история изменила вывод: {out!r}"
    finally:
        _restore(old)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
