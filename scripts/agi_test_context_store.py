#!/usr/bin/env python3
"""agi_test_context_store.py — тесты agi_context_store (изолированы в tmpdir).

Покрытие: round-trip save/load, дедуп pending, TTL age-out, retention prune_old,
баг load_context(0), busy_timeout, пустые pending, статистика/отчёт.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_context_store as cs

TMP = tempfile.mkdtemp(prefix="agi_ctx_test_")
cs.DB_PATH = Path(TMP) / "test.db"

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


_TS_COL = {"sessions": "timestamp", "pending_tasks": "created_at", "context_snapshots": "timestamp"}


def _backdate(table, row_id, seconds_ago):
    """Сдвинуть timestamp записи в прошлое."""
    with cs._get_conn() as conn:
        conn.execute(
            f"UPDATE {table} SET {_TS_COL[table]} = ? WHERE id = ?",
            (time.time() - seconds_ago, row_id),
        )
        conn.commit()


def test_roundtrip():
    print("roundtrip save/load")
    sid = cs.save_context({
        "last_task": "fix gateway timeout",
        "session_phase": "running",
        "tool_call_count": 42,
        "swarm_size": 7,
        "active_projects": ["paperclip", "finforge"],
        "user_preferences": {"lang": "ru"},
        "modified_files": ["a.py", "b.py"],
    })
    ctx = cs.load_context(sid)
    check("session_id > 0", sid > 0)
    check("last_task", ctx["last_task"] == "fix gateway timeout")
    check("phase", ctx["session_phase"] == "running")
    check("tool_call_count", ctx["tool_call_count"] == 42)
    check("active_projects list", ctx["active_projects"] == ["paperclip", "finforge"])
    check("user_preferences dict", ctx["user_preferences"] == {"lang": "ru"})
    check("modified_files", ctx["modified_files"] == ["a.py", "b.py"])

    sid2 = cs.save_context({"last_task": "second"})
    check("ids increment", sid2 > sid)
    check("load latest", cs.load_context()["last_task"] == "second")
    check("load explicit", cs.load_context(sid)["last_task"] == "fix gateway timeout")


def test_load_zero_bug():
    print("load_context(0) не подменяется последней")
    cs.save_context({"last_task": "zzz"})
    ctx = cs.load_context(0)
    check("id=0 -> {} (нет сессии 0)", ctx == {})


def test_pending_dedup():
    print("pending: дедуп + валидация")
    cs.save_context({"pending_tasks": ["Deploy Bot", "DEPLOY BOT", "  ", None, 42]})
    with cs._get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM pending_tasks").fetchone()["c"]
    check("дубли/пустые схлопнуты до 1", n == 1)

    check("add new -> True", cs.add_pending_task("New Task"))
    check("add dup (case) -> False", not cs.add_pending_task("new task"))
    check("add empty -> False", not cs.add_pending_task("   "))
    check("add None -> False", not cs.add_pending_task(None))

    check("rm -> True", cs.remove_pending_task("NEW TASK"))
    check("rm missing -> False", not cs.remove_pending_task("Nope"))
    check("rm empty -> False", not cs.remove_pending_task(""))


def test_ttl():
    print("TTL: age_out + фильтр при загрузке")
    tid = None
    with cs._get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_tasks (task_hash, task, created_at) VALUES (?, ?, ?)",
            ("oldhash", "Old Task", time.time()),
        )
        tid = cur.lastrowid
        conn.commit()
    _backdate("pending_tasks", tid, cs.TASK_TTL_HOURS * 3600 + 60)

    check("age_out удалил 1", cs.age_out_tasks() == 1)
    check("aged не грузится", all(t != "Old Task" for t in cs.load_context()["pending_tasks"]))

    cs.add_pending_task("Fresh Task")
    check("свежая в load", "Fresh Task" in cs.load_context()["pending_tasks"])


def test_snapshots():
    print("снапшоты: только complete/interrupted/error")
    cs.save_context({"last_task": "snap1", "session_phase": "running"})
    cs.save_context({"last_task": "snap2", "session_phase": "complete"})
    with cs._get_conn() as conn:
        rows = conn.execute("SELECT snapshot_json FROM context_snapshots").fetchall()
    check("1 снапшот (complete)", len(rows) == 1)
    data = json.loads(rows[0]["snapshot_json"])
    check("снапшот валиден", data["last_task"] == "snap2")


def fresh_db():
    """Переключить хранилище на чистый файл (изоляция тестов друг от друга)."""
    global TMP
    cs.DB_PATH = Path(tempfile.mkdtemp(prefix="agi_ctx_test_")) / "test.db"


def test_prune():
    print("prune_old: retention сессий и снапшотов")
    fresh_db()
    s1 = cs.save_context({"last_task": "oldest", "session_phase": "complete"})
    s2 = cs.save_context({"last_task": "mid", "session_phase": "complete"})
    s3 = cs.save_context({"last_task": "newest", "session_phase": "complete"})
    # состарим s1 (и её снапшот) и снапшот s2
    _backdate("sessions", s1, 10 * 86400)
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE context_snapshots SET timestamp = ? WHERE session_id IN (?, ?)",
            (time.time() - 10 * 86400, s1, s2),
        )
        conn.commit()

    res = cs.prune_old(max_sessions=2, snapshot_days=7)
    check("удалена 1 сессия", res["sessions"] == 1)
    check("удалены 2 снапшота", res["snapshots"] == 2)

    with cs._get_conn() as conn:
        sessions = conn.execute("SELECT id FROM sessions").fetchall()
        snaps = conn.execute("SELECT session_id FROM context_snapshots").fetchall()
    check("остались s2,s3", sorted(r["id"] for r in sessions) == sorted([s2, s3]))
    check("снапшот только у s3", [r["session_id"] for r in snaps] == [s3])


def test_busy_timeout():
    print("busy_timeout прагма")
    with cs._get_conn() as conn:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    check("busy_timeout=3000", val == 3000)


def test_stats_report():
    print("stats + report")
    fresh_db()
    cs.save_context({"last_task": "statcheck"})
    stats = cs.get_stats()
    check("stats поля", stats["total_sessions"] >= 1 and "db_size_kb" in stats)
    report = cs.get_report()
    check("report содержит Context Store", "Context Store" in report)
    check("report содержит последнюю задачу", "statcheck" in report)


if __name__ == "__main__":
    test_roundtrip()
    test_load_zero_bug()
    test_pending_dedup()
    test_ttl()
    test_snapshots()
    test_prune()
    test_busy_timeout()
    test_stats_report()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
