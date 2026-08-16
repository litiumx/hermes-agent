#!/usr/bin/env python3
"""agi_test_diff_ignore_noise.py — tool_call_count не должен шуметь в диффах.

Покрывает кандидат цикла 35:
1. _compute_diff игнорирует tool_call_count (монотонный счётчик, меняется
   при КАЖДОМ сохранении — в диффе это шум)
2. save_context с изменением ТОЛЬКО tool_call_count → "no changes"
3. Реальные изменения (last_task) по-прежнему видны в diff
4. tool_call_count НЕ теряется при сохранении (архивация/хранение не тронуты)
5. Edge: отсутствие tool_call_count в prev/curr не ломает diff
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
    tmp = Path(tempfile.mkdtemp(prefix="bridge_diff_noise_"))
    sb.SESSION_DIR = tmp
    sb.HISTORY_DIR = tmp / "history"
    sb.BRIDGE_FILE = sb.SESSION_DIR / "bridge.json"
    sb._USE_SQLITE = False  # принудительно JSON-бэкенд (изоляция)

    print("== 1. _compute_diff игнорирует tool_call_count ==")
    prev = sb._canonicalize({"last_task": "t1", "tool_call_count": 1})
    curr = sb._canonicalize({"last_task": "t1", "tool_call_count": 2})
    diff = sb._compute_diff(prev, curr)
    check("diff пуст при изменении только счётчика", diff == {}, f"-> {diff}")

    print("== 2. save_context: только счётчик → no changes ==")
    sb.save_context({"last_task": "t1", "session_phase": "active",
                     "tool_call_count": 1}, snapshot=False)
    out = sb.save_context({"last_task": "t1", "session_phase": "active",
                           "tool_call_count": 2}, snapshot=False)
    check("возвращено 'no changes'", "no changes" in out, f"-> {out!r}")

    print("== 3. реальные изменения видны в diff ==")
    out = sb.save_context({"last_task": "t2", "session_phase": "active",
                           "tool_call_count": 3}, snapshot=False)
    check("last_task виден в diff", "last_task" in out and "tool_call_count" not in out,
          f"-> {out!r}")

    print("== 4. tool_call_count хранится (не теряется) ==")
    ctx = sb.load_context()
    check("счётчик сохранён в контексте", ctx.get("tool_call_count") == 3,
          f"-> {ctx.get('tool_call_count')}")

    print("== 5. edge: отсутствие счётчика в prev/curr ==")
    diff = sb._compute_diff(sb._canonicalize({"last_task": "t1"}),
                            sb._canonicalize({"last_task": "t1", "tool_call_count": 0}))
    check("нет шума при появлении счётчика", diff == {}, f"-> {diff}")
    diff = sb._compute_diff(sb._canonicalize({"last_task": "t1", "tool_call_count": 5}),
                            sb._canonicalize({"last_task": "t1"}))
    check("нет шума при исчезновении счётчика", diff == {}, f"-> {diff}")

    print("== 6. edge: счётчик + реальное изменение ==")
    diff = sb._compute_diff(sb._canonicalize({"last_task": "t1", "tool_call_count": 1}),
                            sb._canonicalize({"last_task": "t2", "tool_call_count": 9}))
    check("виден только last_task", diff == {"last_task": {"old": "t1", "new": "t2"}},
          f"-> {diff}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
