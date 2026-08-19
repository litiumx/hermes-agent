#!/usr/bin/env python3
"""agi_test_focus_escalation.py — эскалация пользователю при повторном
срабатывании detect_repeated_calls ПОСЛЕ компакции (grow point 19.08):
компакция была <COMPACT_COOLDOWN_H назад, а повторы ВСЁ ЕЩЕ есть → компакция
не сломала цикл → escalate_loop() пишет escalation-событие, auto_focus_cycle
возвращает action=repeat_escalated (не тихий repeat_cooldown).
Спам-защита: не чаще ESCALATION_COOLDOWN_H (24ч). Все тесты в tempdir."""
import json, os, sys, sqlite3, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_focus_agent as f

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def make_env():
    tmp = Path(tempfile.mkdtemp(prefix="agi_focus_esc_"))
    f.KB_FILE = tmp / "kb.json"
    f.HISTORY_FILE = tmp / "hist.json"
    f.save_kb({"created": datetime.now().isoformat(), "knowledge": []})
    return tmp


def make_db(rows):
    tmp = Path(tempfile.mkdtemp(prefix="agi_focus_esc_db_"))
    db = tmp / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
        content TEXT, tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
    now = time.time()
    for role, tc, age_sec in rows:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
            ("s1", role, "c", json.dumps(tc), str(now - age_sec)))
    conn.commit()
    conn.close()
    return db


def tc(name, args):
    return {"name": name, "arguments": json.dumps(args)}


def loop_db():
    """БД с 3 одинаковыми search-вызовами в окне (подтверждённый повтор)."""
    return make_db([("assistant", [tc("search", {"q": "loop"})], 60),
                    ("assistant", [tc("search", {"q": "loop"})], 120),
                    ("assistant", [tc("search", {"q": "loop"})], 180)])


print("== 1. escalate_loop пишет escalation-событие в history ==")
make_env()
r = f.escalate_loop([{"tool": "search", "args": {"q": "loop"}, "count": 3}])
check("возвращает message", isinstance(r.get("message"), str) and len(r["message"]) > 10, str(r))
check("tool в результате", r.get("tool") == "search", str(r))
check("count в результате", r.get("count") == 3, str(r))
hist = json.load(open(f.HISTORY_FILE))
check("событие type=escalation", hist[-1]["type"] == "escalation", str(hist[-1]))
check("tool в событии", hist[-1]["tool"] == "search", str(hist[-1]))
check("message в событии", "search" in hist[-1].get("message", ""), str(hist[-1]))

print("== 2. escalate_loop: пустые repeats → тихий отказ без записи ==")
make_env()
r = f.escalate_loop([])
check("message пустой", r.get("message") == "", str(r))
check("history не создан", not f.HISTORY_FILE.exists())

print("== 3. escalate_loop: top_hit используется (не repeats[0]) ==")
make_env()
r = f.escalate_loop([{"tool": "a", "args": {}, "count": 1},
                     {"tool": "b", "args": {}, "count": 2}],
                    top_hit={"tool": "b", "args": {}, "count": 2})
check("top_hit в приоритете", r.get("tool") == "b", str(r))

print("== 4. auto_focus_cycle: повторы + СВЕЖАЯ компакция → repeat_escalated ==")
make_env()
f.SESSION_STATE = loop_db()
f.get_context_usage = lambda: (100, 50_000)  # контекст мал — не порог
f._log_event({"time": datetime.now().isoformat(), "type": "compaction"})
r = f.auto_focus_cycle()
check("action=repeat_escalated", r["action"] == "repeat_escalated", str(r.get("action")))
check("escalation в результате", r.get("escalation", {}).get("tool") == "search", str(r))
check("advice = сообщение эскалации", "не сломала" in r.get("advice", ""), str(r.get("advice")))
hist = json.load(open(f.HISTORY_FILE))
check("escalation-событие записано", hist[-1]["type"] == "escalation", str(hist[-1]))

print("== 5. auto_focus_cycle: эскалация не спамит (кулдаун 24ч) ==")
r2 = f.auto_focus_cycle()
check("повторный вызов → repeat_cooldown", r2["action"] == "repeat_cooldown", str(r2.get("action")))
hist = json.load(open(f.HISTORY_FILE))
check("новых escalation НЕТ", sum(1 for e in hist if e["type"] == "escalation") == 1, str(hist))

print("== 6. auto_focus_cycle: старая эскалация (25ч) → снова repeat_escalated ==")
make_env()
f.SESSION_STATE = loop_db()
f.get_context_usage = lambda: (100, 50_000)
f._log_event({"time": datetime.now().isoformat(), "type": "compaction"})
f._log_event({"time": (datetime.now() - timedelta(hours=25)).isoformat(),
              "type": "escalation", "tool": "old", "count": 1, "message": "old"})
r = f.auto_focus_cycle()
check("action=repeat_escalated (кулдаун прошёл)", r["action"] == "repeat_escalated", str(r.get("action")))
hist = json.load(open(f.HISTORY_FILE))
check("новая escalation записана", sum(1 for e in hist if e["type"] == "escalation") == 2, str(hist))

print("== 7. auto_focus_cycle: повторы БЕЗ свежей компакции → компакция, НЕ эскалация ==")
make_env()
f.SESSION_STATE = loop_db()
f.get_context_usage = lambda: (100, 50_000)
f._log_event({"time": (datetime.now() - timedelta(hours=50)).isoformat(), "type": "compaction"})
r = f.auto_focus_cycle()
check("action в (compacted,repeat_advised)", r["action"] in ("compacted", "repeat_advised"), str(r.get("action")))
check("escalation НЕ в результате", "escalation" not in r, str(r))

print("== 8. escalate_loop устойчив к битым входным данным ==")
make_env()
r = f.escalate_loop([{"tool": "x", "count": 1}])
check("нет краха, message есть", "x" in r.get("message", ""), str(r))
r = f.escalate_loop([{"count": 5}])  # нет tool
check("нет краха при отсутствии tool", isinstance(r.get("message", ""), str), str(r))

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
