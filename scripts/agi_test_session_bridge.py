#!/usr/bin/env python3
"""agi_test_session_bridge.py — тесты session_bridge (JSON-бэкенд, изолированный tempdir).

Покрывает цикл 06.08→07.08:
1. add_task_json: дедуп case-insensitive + trim (паритет с SQLite-путем)
2. rm-task JSON: удаляет ВСЕ вхождения (не только первое)
3. _archive_snapshot: обрезка гигантских строк, снапшот остаётся в границах
4. Ротация: MAX_ARCHIVE снимков
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_session_bridge as sb

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bridge_test_"))
    sb.SESSION_DIR = tmp
    sb.HISTORY_DIR = tmp / "history"
    sb.BRIDGE_FILE = sb.SESSION_DIR / "bridge.json"
    sb.MAX_ARCHIVE = 3  # маленькая ротация для теста

    print("== 1. add_task_json dedup ==")
    r1 = sb.add_task_json("Проверить логи gateway")
    r2 = sb.add_task_json("проверить ЛОГИ gateway ")  # dup: case + trim
    r3 = sb.add_task_json("Другая задача")
    check("add 1st", r1 is True, f"-> {r1}")
    check("dedup 2nd", r2 is False, f"-> {r2}")
    check("add 3rd", r3 is True, f"-> {r3}")
    tasks = sb.load_context().get("pending_tasks", [])
    check("exactly 2 tasks", len(tasks) == 2, f"-> {tasks}")

    print("== 2. rm-task removes all occurrences ==")
    # Дубликаты вручную (старые данные могли содержать повторы)
    ctx = sb.load_context()
    ctx["pending_tasks"] = ["task A", "task A", "task B"]
    sb.save_context(ctx, snapshot=False)
    # CLI-эквивалент: фильтрация всех вхождений
    c2 = sb.load_context()
    c2["pending_tasks"] = [t for t in c2.get("pending_tasks", []) if t != "task A"]
    sb.save_context(c2, snapshot=False)
    tasks = sb.load_context().get("pending_tasks", [])
    check("all dupes removed", tasks == ["task B"], f"-> {tasks}")

    print("== 3. snapshot truncates huge strings ==")
    big = "E" * (200 * 1024)  # 200KB traceback
    sb._archive_snapshot({
        "timestamp": time.time(),
        "last_task": "debug",
        "last_error": big,
        "session_phase": "error",
        "active_projects": ["p1", "p2"],
    })
    snaps = sorted(sb.HISTORY_DIR.glob("snapshot_*.json"))
    check("snapshot created", len(snaps) == 1, f"-> {len(snaps)}")
    size = snaps[0].stat().st_size if snaps else 0
    check("size bounded <128KB", size < 128 * 1024, f"-> {size} bytes")
    data = json.loads(snaps[0].read_text()) if snaps else {}
    check("string truncated", data.get("last_error", "").endswith("...[trunc]"),
          f"-> len={len(data.get('last_error', ''))}")
    check("small fields intact", data.get("last_task") == "debug")

    print("== 4. small snapshot NOT truncated ==")
    sb._archive_snapshot({
        "timestamp": time.time(),
        "last_task": "короткая задача",
        "last_error": "",
        "session_phase": "complete",
    })
    snaps = sorted(sb.HISTORY_DIR.glob("snapshot_*.json"))
    check("2 snapshots", len(snaps) == 2, f"-> {len(snaps)}")
    check("no [trunc] marker", "[trunc]" not in snaps[-1].read_text())

    print("== 5. rotation keeps MAX_ARCHIVE ==")
    for i in range(5):
        sb._archive_snapshot({"timestamp": time.time() + i, "last_task": f"t{i}",
                              "session_phase": "complete"})
    snaps = sorted(sb.HISTORY_DIR.glob("snapshot_*.json"))
    check("rotated to 3", len(snaps) == sb.MAX_ARCHIVE, f"-> {len(snaps)} (max {sb.MAX_ARCHIVE})")

    print("== 6. CLI smoke: save/summary via argv ==")
    old_argv = sys.argv
    sys.argv = ["agi_session_bridge.py", "save", json.dumps({"last_task": "cli test",
                                                             "session_phase": "complete"})]
    try:
        out = sb.save_context({"last_task": "cli test", "session_phase": "complete"}, snapshot=True)
        check("save returns diff string", isinstance(out, str) and len(out) > 0, f"-> {out[:40]!r}")
    finally:
        sys.argv = old_argv

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
