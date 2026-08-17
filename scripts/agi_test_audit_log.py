#!/usr/bin/env python3
"""agi_test_audit_log.py — тесты append-only audit лога (OptMem-паттерн).

Паттерн (SELF_IMPROVE 2026-08-10, APPLY #3, OptMem 1205★):
- лог ТОЛЬКО append-only, существующие строки никогда не редактируются
- summary ПЕРЕСОБИРАЕТСЯ из лога (rebuild) — лог = источник истины
- integrity: любой UPDATE существующей строки = коррупция

Покрытие: append/get, валидация op, rebuild идемпотентен, integrity
(свежий/stale/коррумпированный лог), интеграция с мутациями
(save/add/remove/age_out/prune), не ломает существующее поведение.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_context_store as cs

# Все temp-директории теста регистрируются и удаляются при выходе —
# иначе каждый прогон оставляет agi_audit_iso_* в /tmp (мусор 96% tmpfs).
_TMP_DIRS = []


def _track(tmp):
    _TMP_DIRS.append(tmp)
    return tmp


def _cleanup_tmp():
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_tmp)

TMP = _track(tempfile.mkdtemp(prefix="agi_audit_test_"))
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


def fresh_db():
    """Изолированная БД для тестов с абсолютными счётчиками."""
    cs.DB_PATH = Path(_track(tempfile.mkdtemp(prefix="agi_audit_iso_"))) / "iso.db"


def test_append_get():
    print("append/get roundtrip")
    cs.append_audit("session_save", {"session_id": 1, "phase": "complete"})
    cs.append_audit("task_add", {"task": "fix bug"})
    cs.append_audit("prune", {"sessions": 2})
    log = cs.get_audit(limit=10)
    check("3 события", len(log) == 3)
    check("новые первыми", log[0]["op"] == "prune")
    check("payload json", log[1]["payload"].get("task") == "fix bug")
    check("поле ts", isinstance(log[0]["ts"], float))
    check("поле id", log[2]["id"] < log[0]["id"])


def test_append_invalid_op():
    print("invalid op rejected")
    n0 = len(cs.get_audit(limit=100))
    check("пустой op", cs.append_audit("") == 0)
    check("None op", cs.append_audit(None) == 0)
    check("не-строка", cs.append_audit(123) == 0)
    n1 = len(cs.get_audit(limit=100))
    check("лог не вырос", n1 == n0)


def test_append_payload_default():
    print("payload default")
    cs.append_audit("ping")  # без payload
    row = cs.get_audit(limit=1)[0]
    check("payload = {}", row["payload"] == {})


def test_rebuild_summary():
    print("rebuild summary")
    fresh_db()
    cs.append_audit("task_add", {"task": "a"})
    cs.append_audit("task_add", {"task": "b"})
    cs.append_audit("session_save", {"session_id": 7})
    s = cs.rebuild_summary()
    check("total = 3", s["total_events"] == 3)
    check("op_counts.task_add = 2", s["op_counts"].get("task_add") == 2)
    check("op_counts.session_save = 1", s["op_counts"].get("session_save") == 1)
    check("last_id > 0", s["last_id"] > 0)


def test_rebuild_idempotent():
    print("rebuild idempotent")
    fresh_db()
    cs.append_audit("ping")
    s1 = cs.rebuild_summary()
    s2 = cs.rebuild_summary()
    check("total совпадает", s1["total_events"] == s2["total_events"])
    check("op_counts совпадает", s1["op_counts"] == s2["op_counts"])


def test_summary_zero():
    print("empty db summary")
    fresh = _track(tempfile.mkdtemp(prefix="agi_audit_zero_"))
    old_path = cs.DB_PATH
    cs.DB_PATH = Path(fresh) / "z.db"
    try:
        s = cs.get_audit_summary()
        check("нулевой total", s["total_events"] == 0)
        s2 = cs.rebuild_summary()
        check("rebuild пустого", s2["total_events"] == 0 and s2["op_counts"] == {})
    finally:
        cs.DB_PATH = old_path


def test_integrity_fresh():
    print("integrity fresh")
    fresh_db()
    cs.append_audit("session_save", {})
    cs.rebuild_summary()
    r = cs.audit_integrity()
    check("ok=True", r["ok"] is True)
    check("stale=False", r["stale"] is False)
    check("log_events == summary_events", r["log_events"] == r["summary_events"])


def test_integrity_stale_summary():
    print("integrity stale summary")
    fresh_db()
    cs.append_audit("task_add", {"task": "after rebuild"})
    r = cs.audit_integrity()
    check("ok=True (лога цел)", r["ok"] is True)
    check("stale=True", r["stale"] is True)
    check("summary_events < log_events",
          r["summary_events"] < r["log_events"])


def test_integrity_corrupt_row():
    print("integrity corrupt row")
    fresh_db()
    cs.append_audit("task_add", {"task": "victim"})
    # Прямой UPDATE существующей строки = нарушение append-only инварианта
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE audit_log SET payload_json = 'NOT_JSON{{{' WHERE id = ?",
            (cs.get_audit(limit=100)[-1]["id"],),
        )
        conn.commit()
    r = cs.audit_integrity()
    check("ok=False", r["ok"] is False)
    check("issue найдена", len(r["issues"]) >= 1)
    check("коррупция в issues", any("payload" in i for i in r["issues"]))


def test_mutations_audited():
    print("mutations write audit")
    fresh_db()
    cs.save_context({"last_task": "audited", "session_phase": "complete"})
    cs.add_pending_task("audit task one")
    cs.add_pending_task("audit task one")  # дубль — не должен логироваться
    cs.remove_pending_task("audit task one")
    ops = [e["op"] for e in cs.get_audit(limit=100)]
    check("session_save в логе", "session_save" in ops)
    check("task_add в логе", "task_add" in ops)
    check("task_remove в логе", "task_remove" in ops)
    check("дубль task_add не залогирован", ops.count("task_add") == 1)


def test_audit_does_not_break_existing():
    print("existing behavior intact")
    ctx = cs.load_context()
    check("load_context работает", "last_task" in ctx)
    stats = cs.get_stats()
    check("stats работает", stats["total_sessions"] >= 1)
    check("stats содержит audit_events", "audit_events" in stats)


if __name__ == "__main__":
    test_append_get()
    test_append_invalid_op()
    test_append_payload_default()
    test_rebuild_summary()
    test_rebuild_idempotent()
    test_summary_zero()
    test_integrity_fresh()
    test_integrity_stale_summary()
    test_integrity_corrupt_row()
    test_mutations_audited()
    test_audit_does_not_break_existing()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
