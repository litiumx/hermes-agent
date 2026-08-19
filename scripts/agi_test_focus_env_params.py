#!/usr/bin/env python3
"""agi_test_focus_env_params.py — параметризация loop-детектора через env
(grow point 41, цикл 46): AGI_REPEAT_WINDOW_SEC / AGI_REPEAT_MIN /
AGI_REPEAT_TOP настраивают окно/порог/топ детектора повторов tool calls
БЕЗ правки кода. Явные аргументы вызова имеют приоритет над env; мусор
в env (пусто/не-число/отрицательное) безопасно фолбэчится на дефолты.
Все тесты на temp-БД, без сети."""
import json, os, sys, sqlite3, tempfile, time
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
    """temp state.db со схемой session_logger; rows=(role, tool_calls, age_sec)."""
    tmp = Path(tempfile.mkdtemp(prefix="agi_env_"))
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


ENV_KEYS = ("AGI_REPEAT_WINDOW_SEC", "AGI_REPEAT_MIN", "AGI_REPEAT_TOP")


def clear_env():
    for k in ENV_KEYS:
        os.environ.pop(k, None)


print("== 1. Окно через env: AGI_REPEAT_WINDOW_SEC сужает окно ==")
clear_env()
db = make_db([("assistant", [tc("search", {"q": "same"})], 60),
              ("assistant", [tc("search", {"q": "same"})], 120),
              ("assistant", [tc("search", {"q": "same"})], 180)])
os.environ["AGI_REPEAT_WINDOW_SEC"] = "100"  # в окне только 2 из 3 → нет повтора
r = f.detect_repeated_calls(db_path=db)
check("окно=100 → [] (2 вызова в окне < порога 3)", r == [], str(r))
os.environ["AGI_REPEAT_WINDOW_SEC"] = "300"  # все 3 в окне → детект
r = f.detect_repeated_calls(db_path=db)
check("окно=300 → детект count=3", len(r) == 1 and r[0]["count"] == 3, str(r))
clear_env()
r = f.detect_repeated_calls(db_path=db)
check("env снят → дефолт 7200 → детект", len(r) == 1, str(r))

print("== 2. Порог через env: AGI_REPEAT_MIN ==")
clear_env()
db3 = make_db([("assistant", [tc("search", {"q": "same"})], 60),
               ("assistant", [tc("search", {"q": "same"})], 120),
               ("assistant", [tc("search", {"q": "same"})], 180)])
os.environ["AGI_REPEAT_MIN"] = "4"
check("min=4 при 3 вызовах → []", f.detect_repeated_calls(db_path=db3) == [])
db4 = make_db([("assistant", [tc("search", {"q": "same"})], 60),
               ("assistant", [tc("search", {"q": "same"})], 120),
               ("assistant", [tc("search", {"q": "same"})], 180),
               ("assistant", [tc("search", {"q": "same"})], 240)])
r = f.detect_repeated_calls(db_path=db4)
check("min=4 при 4 вызовах → детект count=4",
      len(r) == 1 and r[0]["count"] == 4, str(r))
clear_env()

print("== 3. Топ через env: AGI_REPEAT_TOP ограничивает вывод ==")
clear_env()
dbm = make_db([("assistant", [tc("search", {"q": "a"})], 60),
               ("assistant", [tc("search", {"q": "a"})], 120),
               ("assistant", [tc("search", {"q": "a"})], 180),
               ("assistant", [tc("read", {"p": "x"})], 60),
               ("assistant", [tc("read", {"p": "x"})], 120),
               ("assistant", [tc("read", {"p": "x"})], 180)])
r = f.detect_repeated_calls(db_path=dbm)
check("дефолт top=5 → 2 сигнатуры", len(r) == 2, str(r))
os.environ["AGI_REPEAT_TOP"] = "1"
r = f.detect_repeated_calls(db_path=dbm)
check("top=1 → 1 сигнатура", len(r) == 1, str(r))
clear_env()

print("== 4. Явные аргументы имеют приоритет над env ==")
os.environ["AGI_REPEAT_MIN"] = "4"
r = f.detect_repeated_calls(db_path=db3, min_repeats=3)
check("env min=4, но явный min_repeats=3 → детект", len(r) == 1, str(r))
r = f.detect_repeated_calls(db_path=db3)
check("без явного аргумента env min=4 → []", r == [], str(r))
clear_env()

print("== 5. Мусор в env → дефолты (без краха) ==")
clear_env()
os.environ["AGI_REPEAT_MIN"] = "abc"
check("min=abc → дефолт 3 → детект", len(f.detect_repeated_calls(db_path=db3)) == 1)
os.environ["AGI_REPEAT_WINDOW_SEC"] = ""
check("window='' → дефолт 7200 → детект", len(f.detect_repeated_calls(db_path=db3)) == 1)
os.environ["AGI_REPEAT_TOP"] = "x"
check("top=x → дефолт 5 → 1 сигнатура", len(f.detect_repeated_calls(db_path=db3)) == 1)
clear_env()

print("== 6. Клэмпы: 0/отрицательное не ломают детектор ==")
clear_env()
os.environ["AGI_REPEAT_MIN"] = "0"  # клэмп → 1: одиночный вызов = повтор
db1 = make_db([("assistant", [tc("search", {"q": "one"})], 60)])
r = f.detect_repeated_calls(db_path=db1)
check("min=0 → клэмп 1 → одиночный вызов детектится", len(r) == 1, str(r))
os.environ["AGI_REPEAT_TOP"] = "0"  # клэмп → 1
r = f.detect_repeated_calls(db_path=dbm)
check("top=0 → клэмп 1 → 1 сигнатура", len(r) == 1, str(r))
os.environ["AGI_REPEAT_WINDOW_SEC"] = "-5"  # клэмп → 0: всё старше now → []
r = f.detect_repeated_calls(db_path=db3)
check("window=-5 → клэмп 0 → [] без краха", r == [], str(r))
clear_env()

print("== 7. Полный цикл auto_focus_cycle() читает env ==")
clear_env()
tmpd = Path(tempfile.mkdtemp(prefix="agi_env_cycle_"))
dbcy = make_db([("assistant", [tc("search", {"q": "same"})], 60),
                ("assistant", [tc("search", {"q": "same"})], 120),
                ("assistant", [tc("search", {"q": "same"})], 180)])
old_state, old_kb, old_hist = f.SESSION_STATE, f.KB_FILE, f.HISTORY_FILE
try:
    f.SESSION_STATE = dbcy
    f.KB_FILE = tmpd / "kb.json"
    f.HISTORY_FILE = tmpd / "hist.json"
    os.environ["AGI_REPEAT_MIN"] = "4"
    res = f.auto_focus_cycle()
    check("цикл: env min=4 → повторов нет", res["repeated_calls"] == [], str(res.get("repeated_calls")))
    check("цикл: action=none при отсутствии повторов", res["action"] == "none", res.get("action"))
    os.environ["AGI_REPEAT_MIN"] = "3"
    res = f.auto_focus_cycle()
    check("цикл: env min=3 → повтор детектится",
          len(res["repeated_calls"]) == 1 and res["repeated_calls"][0]["count"] == 3,
          str(res.get("repeated_calls")))
    check("цикл: action в repeat-ветке",
          res["action"] in ("compacted", "repeat_advised"), res.get("action"))
finally:
    f.SESSION_STATE, f.KB_FILE, f.HISTORY_FILE = old_state, old_kb, old_hist
    clear_env()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
