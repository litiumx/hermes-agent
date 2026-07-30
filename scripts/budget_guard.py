#!/usr/bin/env python3
"""Budget Guard v2 — контроль затрат с историей и отчётами."""
import json, sys, sqlite3, os
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path("/root/.hermes/state")
STATE_DB = STATE_DIR / "budget_state.db"
KILL_SWITCH = Path("/tmp/hermes_kill_switch")

DAILY_CAP = 1.50
PER_TASK_CAP = 0.25
MONTHLY_CAP = 10.0

def init_db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB))
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS history (date TEXT, spent_usd REAL, month TEXT, month_spent REAL)")
    conn.commit()
    return conn

def load():
    conn = init_db()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT value FROM state WHERE key='budget'").fetchone()
    conn.close()
    today = str(datetime.now().date())
    month = str(datetime.now().strftime("%Y-%m"))
    state = json.loads(row["value"]) if row else {}
    # Archive previous day if date changed
    if state.get("date") and state["date"] != today:
        archive_day(state)
        state = {"date": today, "month": month, "spent_usd": 0.0, "month_spent": state.get("month_spent", 0.0)}
    if not state:
        state = {"date": today, "month": month, "spent_usd": 0.0, "month_spent": 0.0}
    return state

def archive_day(state):
    conn = init_db()
    conn.execute("INSERT INTO history (date, spent_usd, month, month_spent) VALUES (?,?,?,?)",
                 (state["date"], state["spent_usd"], state["month"], state["month_spent"]))
    conn.commit(); conn.close()

def save(state):
    conn = init_db()
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('budget', ?)", (json.dumps(state),))
    conn.commit(); conn.close()

def check(est_usd):
    s = load()
    if s["month_spent"] + est_usd > MONTHLY_CAP:
        KILL_SWITCH.write_text(f"Monthly cap ${MONTHLY_CAP} exceeded")
        print(f"DENY: monthly ${s['month_spent']:.2f}", file=sys.stderr)
        sys.exit(1)
    if s["spent_usd"] + est_usd > DAILY_CAP:
        KILL_SWITCH.write_text(f"Daily cap ${DAILY_CAP} exceeded")
        print(f"DENY: daily ${s['spent_usd']:.2f}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: ${s['spent_usd']:.2f}/{DAILY_CAP}, month ${s['month_spent']:.2f}/{MONTHLY_CAP}")

def record(usd):
    s = load()
    s["spent_usd"] += usd; s["month_spent"] += usd
    save(s)
    print(f"Recorded: ${usd:.4f}, today ${s['spent_usd']:.2f}, month ${s['month_spent']:.2f}")

def report():
    conn = init_db()
    conn.row_factory = sqlite3.Row
    s = load()
    rows = conn.execute("SELECT date, spent_usd FROM history ORDER BY date DESC LIMIT 30").fetchall()
    conn.close()
    print(f"## Budget Report — {datetime.now().strftime('%Y-%m-%d %H:%M MSK')}")
    print(f"Today: ${s['spent_usd']:.2f}/{DAILY_CAP} | Month: ${s['month_spent']:.2f}/{MONTHLY_CAP}")
    print()
    print("| Date | Spent |")
    print("|------|-------|")
    for r in rows:
        print(f"| {r['date']} | ${r['spent_usd']:.2f} |")
    print(f"\nMonthly cap: ${MONTHLY_CAP} | Daily cap: ${DAILY_CAP} | Per-task: ${PER_TASK_CAP}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "check":
        check(float(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif cmd == "record":
        record(float(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif cmd == "status":
        print(json.dumps(load(), indent=2))
    elif cmd == "report":
        report()
    elif cmd == "reset":
        KILL_SWITCH.unlink(missing_ok=True)
        save({"date": str(datetime.now().date()), "month": str(datetime.now().strftime("%Y-%m")), "spent_usd": 0.0, "month_spent": 0.0})
        print("Reset OK")
