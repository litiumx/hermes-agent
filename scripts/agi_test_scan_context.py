#!/usr/bin/env python3
"""Тесты agi_scan_context.py — интеграция session bridge в proactive_scan.

Покрытие:
- cleanup_stale_tasks: удаляет только старые (TTL), legacy без ts не трогает,
  пустой bridge -> 0, отсутствие файла -> 0;
- session_history_lines: сортировка новые->старые, фильтр по окну часов
  (запас >=1ч от границы — урок цикла 33), limit, битые снапшоты, legacy ts=0;
- context_block: заголовок + строки истории, строка cleanup только при >0,
  нет каталога -> дефолт без исключений.
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


# --- cleanup_stale_tasks ---

def test_cleanup_removes_only_stale(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        sb.add_task_json("old task")
        sb.add_task_json("fresh task")
        # sidecar: старое — в прошлом, свежее — только что
        ctx = sb.load_context()
        ctx[sb._TASK_CREATED_KEY]["old task"] = now - 100 * 3600
        sb.save_context(ctx, snapshot=False)

        removed = sc.cleanup_stale_tasks(max_age_hours=48)
        assert removed == 1, f"ожидалось 1 удаление, got {removed}"
        tasks = sb.load_context().get("pending_tasks", [])
        assert tasks == ["fresh task"], f"осталась не та задача: {tasks}"
    finally:
        _restore(old)


def test_cleanup_legacy_task_kept(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        sb.add_task_json("legacy task")
        ctx = sb.load_context()
        del ctx[sb._TASK_CREATED_KEY]["legacy task"]  # legacy: нет ts
        sb.save_context(ctx, snapshot=False)

        removed = sc.cleanup_stale_tasks(max_age_hours=0)
        assert removed == 0
        assert "legacy task" in sb.load_context().get("pending_tasks", [])
    finally:
        _restore(old)


def test_cleanup_empty_and_missing(tmp_path):
    old = _setup(tmp_path)
    try:
        assert sc.cleanup_stale_tasks() == 0  # пустой bridge
        sb.BRIDGE_FILE.unlink(missing_ok=True)
        assert sc.cleanup_stale_tasks() == 0  # файла нет
    finally:
        _restore(old)


# --- session_history_lines ---

def test_history_order_filter_and_limit(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        # 3 свежих + 1 старый (вне окна 24ч, запас > 1ч)
        _snapshot("snapshot_3000000000000.json", now - 1 * 3600, "main", "oldest fresh")
        _snapshot("snapshot_4000000000000.json", now - 2 * 3600, "research", "middle")
        _snapshot("snapshot_5000000000000.json", now - 0.1 * 3600, "coding", "newest")
        _snapshot("snapshot_1000000000000.json", now - 100 * 3600, "stale", "outside window")

        lines = sc.session_history_lines(hours=24, limit=2)
        assert len(lines) == 2, f"limit не сработал: {lines}"
        assert "newest" in lines[0] and "coding" in lines[0]
        assert "middle" in lines[1]
        assert "outside window" not in "\n".join(lines)
    finally:
        _restore(old)


def test_history_legacy_and_corrupt(tmp_path):
    old = _setup(tmp_path)
    try:
        now = time.time()
        _snapshot("snapshot_6000000000000.json", now - 0.5 * 3600, "main", "good")
        # legacy: без timestamp — включается
        (sb.HISTORY_DIR / "snapshot_7000000000000.json").write_text(json.dumps({
            "session_phase": "legacy", "last_task": "old style"}))
        # битый JSON — пропускается
        (sb.HISTORY_DIR / "snapshot_8000000000000.json").write_text("{corrupt!!!")

        lines = sc.session_history_lines(hours=24, limit=10)
        assert len(lines) == 2, f"ожидалось 2 строки, got {len(lines)}: {lines}"
        assert "??.?? ??:??" in lines[0]  # legacy без ts — наверху (сортировка)
        assert "good" in lines[1]
    finally:
        _restore(old)


def test_history_missing_dir(tmp_path):
    old = _setup(tmp_path)
    try:
        sb.HISTORY_DIR = tmp_path / "no_such_history"
        assert sc.session_history_lines(hours=24) == []
    finally:
        _restore(old)


# --- context_block ---

def test_context_block_full(tmp_path, capsys):
    old = _setup(tmp_path)
    try:
        now = time.time()
        _snapshot("snapshot_9000000000000.json", now - 0.5 * 3600, "main", "hello task")
        sb.add_task_json("stale task")
        ctx = sb.load_context()
        ctx[sb._TASK_CREATED_KEY]["stale task"] = now - 100 * 3600
        sb.save_context(ctx, snapshot=False)

        block = sc.context_block(hours=24, limit=5)
        assert "hello task" in block
        assert "Сессии" in block
        assert "очищено" in block.lower() and "1" in block

        sc.main()
        out = capsys.readouterr().out
        assert "hello task" in out
    finally:
        _restore(old)


def test_context_block_empty(tmp_path):
    old = _setup(tmp_path)
    try:
        block = sc.context_block(hours=24, limit=5)
        assert "Сессии" in block
        assert "нет" in block.lower() or "—" in block
        assert "очищено" not in block  # 0 удалений — строку не печатаем
    finally:
        _restore(old)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
