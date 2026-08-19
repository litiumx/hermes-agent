#!/usr/bin/env python3
"""agi_test_focus_cooldown_env.py — кулдауны цикла через env (grow point
цикла 46, цикл 47): AGI_COMPACT_COOLDOWN_H / AGI_SUGGEST_COOLDOWN_H /
AGI_ESCALATION_COOLDOWN_H / AGI_MEMORY_MAINTAIN_COOLDOWN_H настраивают
частоту компакций/советов/эскалаций/ретеншна БЕЗ правки кода.

Явные аргументы auto_focus_cycle() приоритетнее env; мусор в env
(пусто/не-число) → дефолт; отрицательное → 0 (кулдаун отключён).
Все тесты на temp-файлах (kb/hist/state.db/PATTERNS_FILE), без сети.
"""
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


ENV_KEYS = ("AGI_COMPACT_COOLDOWN_H", "AGI_SUGGEST_COOLDOWN_H",
            "AGI_ESCALATION_COOLDOWN_H", "AGI_MEMORY_MAINTAIN_COOLDOWN_H")


def clear_env():
    for k in ENV_KEYS:
        os.environ.pop(k, None)


def make_db(rows):
    """temp state.db; rows=(role, tool_calls, age_sec)."""
    tmp = Path(tempfile.mkdtemp(prefix="agi_cd_"))
    db = tmp / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, role TEXT, content TEXT,
        tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
    now = time.time()
    for i, r in enumerate(rows):
        role, tc, age_sec = r
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
            ("s1", role, "c", json.dumps(tc), str(now - age_sec)))
    conn.commit()
    conn.close()
    return db


def tc(name, args):
    return {"name": name, "arguments": json.dumps(args)}


def make_history(path, events):
    """events = [(type, age_sec), ...] → ISO-таймстампы в history."""
    hist = [{"time": (datetime.now() - timedelta(seconds=age)).isoformat(),
             "type": t} for t, age in events]
    json.dump(hist, open(path, "w"))


def run_cycle(db, hist_events=(), **kwargs):
    """Изолированный прогон auto_focus_cycle: tmp kb/hist, restore после."""
    tmpd = Path(tempfile.mkdtemp(prefix="agi_cd_"))
    old = (f.SESSION_STATE, f.KB_FILE, f.HISTORY_FILE)
    try:
        f.SESSION_STATE = db
        f.KB_FILE = tmpd / "kb.json"
        f.HISTORY_FILE = tmpd / "hist.json"
        if hist_events:
            make_history(f.HISTORY_FILE, hist_events)
        return f.auto_focus_cycle(**kwargs)
    finally:
        f.SESSION_STATE, f.KB_FILE, f.HISTORY_FILE = old


def repeats_db():
    return make_db([("assistant", [tc("search", {"q": "same"})], 60),
                    ("assistant", [tc("search", {"q": "same"})], 120),
                    ("assistant", [tc("search", {"q": "same"})], 180)])


def big_db_fixed():
    tmp = Path(tempfile.mkdtemp(prefix="agi_cd_"))
    db = tmp / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, role TEXT, content TEXT,
        tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
    conn.execute("INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
                 ("s1", "user", "x" * 1000, None, str(time.time())))
    conn.commit()
    conn.close()
    return db


RECENT = [("compaction", 60)]
OLD_COMPACT = [("compaction", 7 * 3600)]
RECENT_SUGG = [("suggestion", 60)]
RECENT_ESC = [("compaction", 60), ("escalation", 60)]
RECENT_MAINT = [("memory_maintain", 60)]


def set_patterns_tmp():
    os.environ["AGI_PATTERNS_FILE"] = os.path.join(
        tempfile.mkdtemp(prefix="agi_cd_ptrn_"), "patterns.json")


print("== 1. _cooldown_h: дефолт/мусор/клэмпы ==")
clear_env()
check("unset → default", f._cooldown_h("AGI_X_CD", 6) == 6)
os.environ["AGI_X_CD"] = "abc"
check("abc → default 6", f._cooldown_h("AGI_X_CD", 6) == 6)
os.environ["AGI_X_CD"] = ""
check("пусто → default 6", f._cooldown_h("AGI_X_CD", 6) == 6)
os.environ["AGI_X_CD"] = "0"
check("0 → 0 (кулдаун отключён)", f._cooldown_h("AGI_X_CD", 6) == 0)
os.environ["AGI_X_CD"] = "-5"
check("-5 → клэмп 0", f._cooldown_h("AGI_X_CD", 6) == 0)
os.environ["AGI_X_CD"] = "12"
check("12 → 12", f._cooldown_h("AGI_X_CD", 12) == 12)
os.environ.pop("AGI_X_CD", None)

print("== 2. AGI_COMPACT_COOLDOWN_H (токен-ветка) ==")
clear_env()
db = big_db_fixed()
old_act = f.TOKEN_ACT
f.TOKEN_ACT = -1.0  # любой токен-объём → компакция-ветка
try:
    os.environ["AGI_COMPACT_COOLDOWN_H"] = "0"
    r = run_cycle(db, RECENT)
    check("env=0 + свежая компакция → компактим",
          r["action"] == "compact_advised", str(r.get("action")))
    os.environ["AGI_COMPACT_COOLDOWN_H"] = "1000"
    r = run_cycle(db, RECENT)
    check("env=1000 + свежая компакция → compact_cooldown",
          r["action"] == "compact_cooldown", str(r.get("action")))
    clear_env()
    r = run_cycle(db, RECENT)
    check("env снят → дефолт 6 → compact_cooldown",
          r["action"] == "compact_cooldown", str(r.get("action")))
    os.environ["AGI_COMPACT_COOLDOWN_H"] = "1000"
    r = run_cycle(db, RECENT, compact_cooldown_h=0)
    check("env=1000, но явный compact_cooldown_h=0 → компактим",
          r["action"] == "compact_advised", str(r.get("action")))
    os.environ["AGI_COMPACT_COOLDOWN_H"] = "5"
    r = run_cycle(db, OLD_COMPACT)
    check("env=5 + компакция 7ч назад → компактим",
          r["action"] == "compact_advised", str(r.get("action")))
finally:
    f.TOKEN_ACT = old_act
    clear_env()

print("== 3. AGI_COMPACT_COOLDOWN_H (repeat-ветка) ==")
clear_env()
set_patterns_tmp()
db = repeats_db()
os.environ["AGI_COMPACT_COOLDOWN_H"] = "0"
r = run_cycle(db, RECENT)
check("env=0: компакция разрешена несмотря на свежую → repeat_advised",
      r["action"] in ("compacted", "repeat_advised"), str(r.get("action")))
check("env=0: эскалации нет", "escalation" not in r, str(r.keys()))
os.environ["AGI_COMPACT_COOLDOWN_H"] = "1000"
r = run_cycle(db, RECENT)
check("env=1000: компакция подавлена → эскалация",
      r["action"] == "repeat_escalated", str(r.get("action")))
clear_env()

print("== 4. AGI_ESCALATION_COOLDOWN_H ==")
clear_env()
db = repeats_db()
os.environ["AGI_ESCALATION_COOLDOWN_H"] = "0"
r = run_cycle(db, RECENT_ESC)
check("env=0 + свежая эскалация → снова эскалируем",
      r["action"] == "repeat_escalated", str(r.get("action")))
os.environ["AGI_ESCALATION_COOLDOWN_H"] = "1000"
r = run_cycle(db, RECENT_ESC)
check("env=1000 → repeat_cooldown", r["action"] == "repeat_cooldown",
      str(r.get("action")))
check("advice честно про env-значение",
      "1000ч назад" in r.get("advice", ""), str(r.get("advice")))
os.environ["AGI_ESCALATION_COOLDOWN_H"] = "1000"
r = run_cycle(db, RECENT_ESC, escalation_cooldown_h=0)
check("env=1000, но явный escalation_cooldown_h=0 → эскалация",
      r["action"] == "repeat_escalated", str(r.get("action")))
clear_env()
r = run_cycle(db, RECENT_ESC)
check("env снят → дефолт 24 → repeat_cooldown",
      r["action"] == "repeat_cooldown", str(r.get("action")))

print("== 5. AGI_SUGGEST_COOLDOWN_H (watch-ветка) ==")
clear_env()
db = big_db_fixed()
old_warn = f.TOKEN_WARN
f.TOKEN_WARN = -1.0  # любой токен-объём → watch-ветка
try:
    os.environ["AGI_SUGGEST_COOLDOWN_H"] = "0"
    r = run_cycle(db, RECENT_SUGG)
    check("env=0 + свежий совет → watch", r["action"] == "watch",
          str(r.get("action")))
    os.environ["AGI_SUGGEST_COOLDOWN_H"] = "1000"
    r = run_cycle(db, RECENT_SUGG)
    check("env=1000 + свежий совет → watch_cooldown",
          r["action"] == "watch_cooldown", str(r.get("action")))
    clear_env()
    r = run_cycle(db, RECENT_SUGG)
    check("env снят → дефолт 3 → watch_cooldown",
          r["action"] == "watch_cooldown", str(r.get("action")))
finally:
    f.TOKEN_WARN = old_warn
    clear_env()

print("== 6. AGI_MEMORY_MAINTAIN_COOLDOWN_H ==")
clear_env()
db = big_db_fixed()
old_maint = f._memory_maintenance
f._memory_maintenance = lambda: {"ok": True}
os.environ["AGI_MAINTAIN_MEMORY"] = "1"
try:
    os.environ["AGI_MEMORY_MAINTAIN_COOLDOWN_H"] = "0"
    r = run_cycle(db, RECENT_MAINT)
    check("env=0 + свежий ретеншн → ретеншн выполнен",
          r.get("memory_maintained") == {"ok": True}, str(r.get("memory_maintained")))
    os.environ["AGI_MEMORY_MAINTAIN_COOLDOWN_H"] = "1000"
    r = run_cycle(db, RECENT_MAINT)
    check("env=1000 → ретеншн пропущен",
          "memory_maintained" not in r, str(r.keys()))
finally:
    f._memory_maintenance = old_maint
    os.environ.pop("AGI_MAINTAIN_MEMORY", None)
    clear_env()

print("== 7. Мусор в env кулдаунов → дефолты ==")
clear_env()
db = repeats_db()
os.environ["AGI_ESCALATION_COOLDOWN_H"] = "x"
r = run_cycle(db, RECENT_ESC)
check("esc=x → дефолт 24 → repeat_cooldown",
      r["action"] == "repeat_cooldown", str(r.get("action")))
os.environ["AGI_COMPACT_COOLDOWN_H"] = ""
old_act2 = f.TOKEN_ACT
f.TOKEN_ACT = -1.0
try:
    r = run_cycle(big_db_fixed(), RECENT)
    check("compact='' → дефолт 6 → compact_cooldown",
          r["action"] == "compact_cooldown", str(r.get("action")))
finally:
    f.TOKEN_ACT = old_act2
clear_env()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
