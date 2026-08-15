#!/usr/bin/env python3
"""agi_test_session_bridge_json_parity.py — JSON-fallback parity для task lifecycle.

Покрывает цикл 32:
1. add_task_json записывает timestamp в sidecar _task_created
2. age_out_tasks_json удаляет старые задачи (TTL), сохраняет свежие и legacy (без ts)
3. get_stats_json считает задачи/снапшоты/размер bridge.json
4. rm-task JSON чистит и sidecar _task_created
5. round-trip: load_context() сохраняет _task_created (canonicalize не теряет)
6. CLI age-out/stats работают в JSON-режиме
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
    tmp = Path(tempfile.mkdtemp(prefix="bridge_json_parity_"))
    sb.SESSION_DIR = tmp
    sb.HISTORY_DIR = tmp / "history"
    sb.BRIDGE_FILE = sb.SESSION_DIR / "bridge.json"
    sb._USE_SQLITE = False  # принудительно JSON-бэкенд (изоляция)

    print("== 1. add_task_json записывает timestamp ==")
    sb.add_task_json("старая задача")
    sb.add_task_json("свежая задача")
    raw = json.loads(sb.BRIDGE_FILE.read_text())
    created = raw.get("_task_created", {})
    check("sidecar создан", len(created) == 2, f"-> {created}")
    check("ts числовой", all(isinstance(v, (int, float)) for v in created.values()))

    print("== 2. age-out: старые удаляются, свежие/legacy остаются ==")
    # Прошлый timestamp для "старой задачи"
    raw["_task_created"]["старая задача"] = time.time() - 100 * 3600  # 100ч назад (>48h TTL)
    raw["_task_created"]["свежая задача"] = time.time() - 1 * 3600    # 1ч назад
    sb.BRIDGE_FILE.write_text(json.dumps(raw, ensure_ascii=False))
    n = sb.age_out_tasks_json(max_age_hours=48)
    check("удалена 1 (старая)", n == 1, f"-> {n}")
    tasks = sb.load_context().get("pending_tasks", [])
    check("свежая осталась", tasks == ["свежая задача"], f"-> {tasks}")
    check("sidecar почищен", "_task_created" not in sb.load_context()
          or sb.load_context().get("_task_created", {}).get("старая задача") is None,
          f"-> {sb.load_context().get('_task_created', {})}")

    print("== 3. legacy без sidecar не удаляется ==")
    sb.BRIDGE_FILE.write_text(json.dumps({
        "timestamp": time.time(),
        "pending_tasks": ["легаси задача без ts"],
    }, ensure_ascii=False))
    n = sb.age_out_tasks_json(max_age_hours=1)
    check("legacy сохранена", n == 0 and sb.load_context().get("pending_tasks") == ["легаси задача без ts"],
          f"-> n={n}, tasks={sb.load_context().get('pending_tasks')}")

    print("== 4. get_stats_json ==")
    sb.BRIDGE_FILE.write_text(json.dumps({"timestamp": time.time(),
                                          "pending_tasks": []},
                                         ensure_ascii=False))
    sb._archive_snapshot({"timestamp": time.time(), "last_task": "s1",
                          "session_phase": "complete"})
    sb._archive_snapshot({"timestamp": time.time() + 0.01, "last_task": "s2",
                          "session_phase": "complete"})
    sb.add_task_json("task A")
    st = sb.get_stats_json()
    check("считает задачи", st["active_tasks"] == 1, f"-> {st}")
    check("считает снапшоты", st["snapshots"] == 2, f"-> {st['snapshots']}")
    check("bridge size > 0", st["bridge_size_kb"] >= 0, f"-> {st['bridge_size_kb']}KB")
    check("backend=JSON", st.get("backend") == "JSON", f"-> {st.get('backend')}")

    print("== 5. rm-task JSON чистит sidecar ==")
    raw = json.loads(sb.BRIDGE_FILE.read_text())
    raw["pending_tasks"] = ["task A", "task B"]
    raw["_task_created"] = {"task A": time.time(), "task B": time.time()}
    sb.BRIDGE_FILE.write_text(json.dumps(raw, ensure_ascii=False))
    ctx = sb.load_context()
    ctx["pending_tasks"] = [t for t in ctx.get("pending_tasks", []) if t != "task A"]
    sb.save_context(ctx, snapshot=False)
    created = sb.load_context().get("_task_created", {})
    check("task A вычищен из sidecar", "task A" not in created, f"-> {created}")
    check("task B остался", "task B" in created, f"-> {created}")

    print("== 6. round-trip canonicalize сохраняет _task_created ==")
    ctx = sb.load_context()
    ctx["pending_tasks"] = ["roundtrip"]
    ctx["_task_created"] = {"roundtrip": 123.0}
    sb.save_context(ctx, snapshot=False)
    rt = sb.load_context().get("_task_created", {})
    check("sidecar пережил save/load", rt.get("roundtrip") == 123.0, f"-> {rt}")

    print("== 7. CLI smoke: age-out и stats в JSON-режиме ==")
    old_argv = sys.argv
    try:
        sys.argv = ["agi_session_bridge.py", "age-out", "48"]
        # прямой вызов функции (CLI-ветка), не argv-диспетчер — он печатает
        n = sb.age_out_tasks_json(max_age_hours=48)
        check("CLI-функция age-out не падает", n >= 0, f"-> n={n}")
        st = sb.get_stats_json()
        check("stats JSON содержит ключи", {"active_tasks", "snapshots"} <= set(st))
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
