#!/usr/bin/env python3
"""agi_test_focus_repeats.py — детект повторяющихся tool calls в state.db
(SELF_IMPROVE 14.08 #2: на ~80K токенов в multi-step прогоне начинаются
повторяющиеся tool calls — ранний сигнал деградации контекста, компактить
первым делом, не дожидаясь порога 70%). Все тесты на temp-БД."""
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


def make_db(rows):
    """Создать temp state.db со схемой session_logger и вернуть путь."""
    tmp = Path(tempfile.mkdtemp(prefix="agi_focus_rep_"))
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


print("== 1. Нет БД → [] (тихий отказ) ==")
old = f.SESSION_STATE
f.SESSION_STATE = Path(tempfile.mkdtemp(prefix="agi_focus_nodb_")) / "nope.db"
check("[] на нет БД", f.detect_repeated_calls() == [])
f.SESSION_STATE = old

print("== 2. Пустая БД → [] ==")
db = make_db([])
check("[] на пустой", f.detect_repeated_calls(db_path=db) == [])

print("== 3. Одиночный вызов → [] ==")
db = make_db([("assistant", [tc("search", {"q": "x"})], 60)])
check("[] на одиночном", f.detect_repeated_calls(db_path=db) == [])

print("== 4. 3 одинаковых вызова в окне → детект ==")
db = make_db([("assistant", [tc("search", {"q": "same"})], 60),
              ("assistant", [tc("search", {"q": "same"})], 120),
              ("assistant", [tc("search", {"q": "same"})], 180)])
r = f.detect_repeated_calls(db_path=db)
check("детект", len(r) == 1, str(r))
check("tool=search", r[0]["tool"] == "search", str(r))
check("count=3", r[0]["count"] == 3, str(r))

print("== 5. Разные аргументы → [] ==")
db = make_db([("assistant", [tc("search", {"q": "a"})], 60),
              ("assistant", [tc("search", {"q": "b"})], 120),
              ("assistant", [tc("search", {"q": "c"})], 180)])
check("[] на разных", f.detect_repeated_calls(db_path=db) == [])

print("== 6. Порядок ключей аргументов не влияет (нормализация) ==")
db = make_db([("assistant", [tc("run", {"a": 1, "b": 2})], 60),
              ("assistant", [tc("run", {"b": 2, "a": 1})], 120),
              ("assistant", [tc("run", {"a": 1, "b": 2})], 180)])
r = f.detect_repeated_calls(db_path=db)
check("детект при перестановке ключей", len(r) == 1, str(r))
check("count=3", r[0]["count"] == 3, str(r))

print("== 7. Старые вызовы вне окна → [] ==")
db = make_db([("assistant", [tc("search", {"q": "old"})], 10_000),
              ("assistant", [tc("search", {"q": "old"})], 10_100),
              ("assistant", [tc("search", {"q": "old"})], 10_200)])
check("[] вне окна", f.detect_repeated_calls(db_path=db) == [])

print("== 8. min_repeats граница (2 из 3) ==")
db = make_db([("assistant", [tc("search", {"q": "x"})], 60),
              ("assistant", [tc("search", {"q": "x"})], 120),
              ("assistant", [tc("other", {"q": "x"})], 180)])
check("[] при 2 повторах", f.detect_repeated_calls(db_path=db) == [])
r = f.detect_repeated_calls(db_path=db, min_repeats=2)
check("детект при min_repeats=2", len(r) == 1, str(r))
check("count=2", r[0]["count"] == 2, str(r))

print("== 9. Битый JSON в tool_calls → пропуск, не крах ==")
db = make_db([("assistant", None, 60),
              ("assistant", "{not json", 120),
              ("assistant", [tc("search", {"q": "x"})], 180)])
r = f.detect_repeated_calls(db_path=db)
check("[] без краха (1 валидный вызов < min)", r == [], str(r))
db2 = make_db([("assistant", "{broken", 60),
               ("assistant", [tc("search", {"q": "x"})], 120),
               ("assistant", [tc("search", {"q": "x"})], 180),
               ("assistant", [tc("search", {"q": "x"})], 240)])
r2 = f.detect_repeated_calls(db_path=db2)
check("детект при битом мусоре + 3 валидных", len(r2) == 1, str(r2))

print("== 10. Не-assistant роли игнорируются ==")
db = make_db([("user", [tc("search", {"q": "x"})], 60),
              ("tool", [tc("search", {"q": "x"})], 120),
              ("assistant", [tc("search", {"q": "x"})], 180)])
check("[] (только 1 assistant)", f.detect_repeated_calls(db_path=db) == [])

print("== 11. Нечитаемый created_at → пропуск ==")
tmp = Path(tempfile.mkdtemp(prefix="agi_focus_badts_"))
db = tmp / "state.db"
conn = sqlite3.connect(db)
conn.execute("""CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    content TEXT, tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
for _ in range(3):
    conn.execute("INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
                 ("s1", "assistant", "c", json.dumps([tc("search", {"q": "x"})]), "not-a-date"))
conn.commit()
conn.close()
check("[] на битых ts", f.detect_repeated_calls(db_path=db) == [])

print("== 11b. ISO-таймстампы (prod session_logger: %Y-%m-%dT%H:%M:%SZ) ==")
tmp = Path(tempfile.mkdtemp(prefix="agi_focus_iso_"))
db = tmp / "state.db"
conn = sqlite3.connect(db)
conn.execute("""CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    content TEXT, tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
iso_now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
for _ in range(3):
    conn.execute("INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
                 ("s1", "assistant", "c", json.dumps([tc("search", {"q": "iso"})]), iso_now))
conn.commit()
conn.close()
r = f.detect_repeated_calls(db_path=db)
check("детект на ISO ts", len(r) == 1, str(r))
check("tool=search (ISO)", r[0]["tool"] == "search", str(r))

print("== 12. Integration: повторы → компакция без большого контекста ==")
f.HISTORY_FILE = Path(tempfile.mkdtemp(prefix="agi_focus_int_")) / "hist.json"
f.KB_FILE = Path(tempfile.mkdtemp(prefix="agi_focus_int_")) / "kb.json"
f.save_kb({"created": datetime.now().isoformat(), "knowledge": []})
db = make_db([("assistant", [tc("search", {"q": "loop"})], 60),
              ("assistant", [tc("search", {"q": "loop"})], 120),
              ("assistant", [tc("search", {"q": "loop"})], 180)])
f.SESSION_STATE = db
f.get_context_usage = lambda: (100, 50_000)  # маленький контекст — не порог
r = f.auto_focus_cycle()
check("action=repeat_advised", r["action"] == "repeat_advised", str(r))
check("repeated_calls в результате", r.get("repeated_calls") is not None, str(r))
check("advice упоминает tool", "search" in r.get("advice", ""), str(r))

print("== 13. Integration: кулдаун компакции → repeat_cooldown ==")
f._log_event({"time": datetime.now().isoformat(), "type": "compaction"})
r = f.auto_focus_cycle()
check("action=repeat_cooldown", r["action"] == "repeat_cooldown", str(r))

print("== 14. Integration: без повторов — обычная логика ==")
db = make_db([("assistant", [tc("search", {"q": "once"})], 60)])
f.SESSION_STATE = db
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("action=none", r["action"] == "none", str(r))
check("repeated_calls=[]", r.get("repeated_calls") == [], str(r))

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
