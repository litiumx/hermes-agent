#!/usr/bin/env python3
"""agi_test_gateway_guard.py — standalone-тесты для agi_gateway_guard.py.

Покрытие: load_json, parse_pid_field, pid_alive, proc_cmdline,
is_gateway_cmdline, check_file_state (все ветки), cmd_clean_stale
(включая PID-reuse — регрессия), cmd_status exit-коды.
Все тесты изолированы в tempdir, реальные /root/.hermes НЕ трогаются.
"""
import json, os, sys, tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_gateway_guard as g

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

ALIVE = os.getpid()
DEAD = 99999999  # pid, которого нет (в sandbox не может быть жив)

print("== 1. load_json: missing / valid / broken ==")
with tempfile.TemporaryDirectory() as tmp:
    check("missing -> None", g.load_json(os.path.join(tmp, "nope.json")) is None)
    good = os.path.join(tmp, "good.json")
    with open(good, "w") as fh:
        json.dump({"pid": 123}, fh)
    check("valid -> dict", g.load_json(good) == {"pid": 123})
    broken = os.path.join(tmp, "broken.json")
    with open(broken, "w") as fh:
        fh.write("{not json!!")
    d = g.load_json(broken)
    check("broken -> _error dict", isinstance(d, dict) and "_error" in d, str(d))

print("== 2. parse_pid_field: прямой / вложенный / мусор ==")
check("прямой pid", g.parse_pid_field({"pid": 42}) == 42)
check("вложенный pid", g.parse_pid_field({"pid": {"pid": 7}}) == 7)
check("нет pid -> None", g.parse_pid_field({"kind": "x"}) is None)
check("не-int -> None", g.parse_pid_field({"pid": "abc"}) is None)
check("None -> None", g.parse_pid_field(None) is None)
check("не-dict -> None", g.parse_pid_field("str") is None)

print("== 3. pid_alive: живой / мёртвый / границы ==")
check("свой pid жив", g.pid_alive(ALIVE))
check("мёртвый pid не жив", not g.pid_alive(DEAD))
check("pid<=0 не жив", not g.pid_alive(0) and not g.pid_alive(-5))

print("== 4. is_gateway_cmdline: распознавание ==")
check("gateway run -> True", g.is_gateway_cmdline("/venv/bin/hermes gateway run --replace"))
check("hermes gateway (слова) -> True", g.is_gateway_cmdline("hermes gateway"))
check("только gateway -> False", not g.is_gateway_cmdline("/bin/gateway daemon"))
check("только hermes -> False", not g.is_gateway_cmdline("hermes status"))
check("python server -> False", not g.is_gateway_cmdline("/usr/bin/python3 server.py"))
check("пусто -> False", not g.is_gateway_cmdline(""))
check("None -> False", not g.is_gateway_cmdline(None))

print("== 5. check_file_state: все ветки ==")
gw_cmdline = "/venv/bin/hermes gateway run --replace"
with tempfile.TemporaryDirectory() as tmp:
    # 5a. отсутствующий файл — норма
    ok, msg = g.check_file_state(os.path.join(tmp, "missing.json"), "missing")
    check("missing -> ok=True", ok, msg)
    # 5b. живой PID + gateway argv -> ok
    good = os.path.join(tmp, "good.json")
    with open(good, "w") as fh:
        json.dump({"pid": ALIVE, "argv": ["hermes", "gateway", "run"]}, fh)
    with patch.object(g, "proc_cmdline", return_value=gw_cmdline):
        ok, msg = g.check_file_state(good, "good")
        check("alive+gateway argv -> ok=True", ok, msg)
    # 5c. живой PID + ЧУЖОЙ argv (PID-reuse) -> проблема
    foreign = os.path.join(tmp, "foreign.json")
    with open(foreign, "w") as fh:
        json.dump({"pid": ALIVE}, fh)
    with patch.object(g, "proc_cmdline", return_value="/usr/bin/python3 server.py"):
        ok, msg = g.check_file_state(foreign, "foreign")
        check("PID-reuse -> ok=False", not ok, msg)
        check("PID-reuse reason", "ДРУГОЙ" in msg, msg)
    # 5d. живой PID + cmdline недоступен -> проблема (не наш)
    with patch.object(g, "proc_cmdline", return_value=None):
        ok, msg = g.check_file_state(foreign, "nocmd")
        check("alive+нет cmdline -> ok=False", not ok, msg)
    # 5e. мёртвый pid -> stale
    stale = os.path.join(tmp, "stale.json")
    with open(stale, "w") as fh:
        json.dump({"pid": DEAD}, fh)
    with patch.object(g, "proc_cmdline", return_value=None):
        ok, msg = g.check_file_state(stale, "stale")
        check("dead pid -> ok=False", not ok, msg)
        check("dead pid reason STALE", "STALE" in msg, msg)
    # 5f. битый JSON -> проблема
    broken = os.path.join(tmp, "broken.json")
    with open(broken, "w") as fh:
        fh.write("{nope")
    ok, msg = g.check_file_state(broken, "broken")
    check("broken json -> ok=False", not ok, msg)
    # 5g. нет pid -> проблема
    nopid = os.path.join(tmp, "nopid.json")
    with open(nopid, "w") as fh:
        json.dump({"kind": "hermes-gateway"}, fh)
    ok, msg = g.check_file_state(nopid, "nopid")
    check("нет pid -> ok=False", not ok, msg)

print("== 6. cmd_clean_stale: удаление stale/битых, KEEP живых, PID-reuse ==")
gw_cmdline = "/venv/bin/hermes gateway run --replace"
with tempfile.TemporaryDirectory() as tmp:
    pid_file = os.path.join(tmp, "gateway.pid")
    lock_file = os.path.join(tmp, "gateway.lock")
    orig_pid, orig_lock = g.PID_FILE, g.LOCK_FILE
    g.PID_FILE, g.LOCK_FILE = pid_file, lock_file
    try:
        # пустая директория -> 0 удалений, exit 0
        rc = g.cmd_clean_stale(None)
        check("нет файлов -> rc=0", rc == 0, str(rc))
        # stale pid -> удалён + бэкап
        with open(pid_file, "w") as fh:
            json.dump({"pid": DEAD}, fh)
        rc = g.cmd_clean_stale(None)
        check("stale удалён", not os.path.exists(pid_file), "файл остался")
        check("бэкап создан", any(f.startswith("gateway.pid.bak.") for f in os.listdir(tmp)))
        check("stale -> rc=1", rc == 1, str(rc))
        # битый JSON -> удалён
        with open(lock_file, "w") as fh:
            fh.write("{broken")
        rc = g.cmd_clean_stale(None)
        check("broken удалён", not os.path.exists(lock_file))
        # живой gateway PID -> KEPT, не удалять
        with open(pid_file, "w") as fh:
            json.dump({"pid": ALIVE}, fh)
        with patch.object(g, "proc_cmdline", return_value=gw_cmdline):
            rc = g.cmd_clean_stale(None)
        check("alive gateway KEPT", os.path.exists(pid_file))
        check("alive gateway -> rc=0", rc == 0, str(rc))
        # PID-reuse: PID жив, но это НЕ gateway -> stale, удалить (РЕГРЕССИЯ)
        with open(pid_file, "w") as fh:
            json.dump({"pid": ALIVE}, fh)
        with patch.object(g, "proc_cmdline", return_value="/usr/bin/python3 server.py"):
            rc = g.cmd_clean_stale(None)
        check("PID-reuse удалён", not os.path.exists(pid_file), "файл остался (баг!)")
        check("PID-reuse -> rc=1", rc == 1, str(rc))
        # PID жив, cmdline недоступен -> консервативно KEPT (нельзя проверить)
        with open(pid_file, "w") as fh:
            json.dump({"pid": ALIVE}, fh)
        with patch.object(g, "proc_cmdline", return_value=None):
            rc = g.cmd_clean_stale(None)
        check("cmdline недоступен -> KEPT", os.path.exists(pid_file))
    finally:
        g.PID_FILE, g.LOCK_FILE = orig_pid, orig_lock

print("== 7. cmd_status: exit-коды ==")
with tempfile.TemporaryDirectory() as tmp:
    orig_pid, orig_lock = g.PID_FILE, g.LOCK_FILE
    g.PID_FILE, g.LOCK_FILE = os.path.join(tmp, "p.json"), os.path.join(tmp, "l.json")
    try:
        # всё чисто: нет файлов, нет процессов -> rc=0
        with patch.object(g, "scan_gateway_processes", return_value=[]):
            rc = g.cmd_status(None)
        check("чисто -> rc=0", rc == 0, str(rc))
        # stale pid файл -> rc=1
        with open(g.PID_FILE, "w") as fh:
            json.dump({"pid": DEAD}, fh)
        with patch.object(g, "scan_gateway_processes", return_value=[]):
            rc = g.cmd_status(None)
        check("stale -> rc=1", rc == 1, str(rc))
        # дубли процессов -> rc=1
        os.remove(g.PID_FILE)
        procs = [{"pid": 1, "cmdline": gw_cmdline, "start": 0.0},
                 {"pid": 2, "cmdline": gw_cmdline, "start": 1.0}]
        with patch.object(g, "scan_gateway_processes", return_value=procs):
            rc = g.cmd_status(None)
        check("дубли -> rc=1", rc == 1, str(rc))
    finally:
        g.PID_FILE, g.LOCK_FILE = orig_pid, orig_lock

print("== 8. scan_gateway_processes: фильтрация по cmdline ==")
procs = [{"pid": 111, "cmdline": gw_cmdline, "start": 0.0},
         {"pid": 222, "cmdline": "/usr/bin/python3 server.py", "start": 1.0}]
with patch.object(g.os, "listdir", return_value=["111", "222", "notapid"]):
    with patch.object(g, "proc_cmdline", side_effect=lambda p: next(x["cmdline"] for x in procs if x["pid"] == p)):
        found = g.scan_gateway_processes()
check("только gateway-процессы", len(found) == 1 and found[0]["pid"] == 111, str(found))

print("== 9. parse_iso_ts: парсинг таймстемпов ==")
from datetime import datetime, timezone, timedelta
check("Z-суффикс", g.parse_iso_ts("2026-08-09T12:00:00Z") == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc))
check("+offset в UTC", g.parse_iso_ts("2026-08-09T15:00:00+03:00") == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc))
check("naive -> UTC", g.parse_iso_ts("2026-08-09T12:00:00") == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc))
check("мусор -> None", g.parse_iso_ts("not-a-date") is None)
check("None -> None", g.parse_iso_ts(None) is None)
check("число -> None", g.parse_iso_ts(123) is None)

print("== 10. check_state_file: gateway_state.json все ветки ==")
now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
old_iso = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec="seconds")
with tempfile.TemporaryDirectory() as tmp:
    sp = os.path.join(tmp, "state.json")
    # 10a. отсутствует — норма
    ok, msg = g.check_state_file(sp, "state")
    check("missing -> ok", ok, msg)
    # 10b. битый JSON -> проблема
    with open(sp, "w") as fh:
        fh.write("{broken")
    ok, msg = g.check_state_file(sp, "state")
    check("broken -> fail", not ok, msg)
    check("broken reason", "БИТЫЙ" in msg, msg)
    # 10c. не-объект -> проблема
    with open(sp, "w") as fh:
        fh.write("[1,2,3]")
    ok, msg = g.check_state_file(sp, "state")
    check("list -> fail", not ok, msg)
    # 10d. нет gateway_state -> проблема
    with open(sp, "w") as fh:
        json.dump({"pid": 1}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("нет поля -> fail", not ok, msg)
    # 10e. свежий running -> ok
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "updated_at": now_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("свежий running -> ok", ok, msg)
    # 10f. протухший running -> STALE fail
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "updated_at": old_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("stale -> fail", not ok, msg)
    check("stale reason", "STALE" in msg, msg)
    # 10g. ключ ts тоже работает
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "ts": old_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("ts stale -> fail", not ok, msg)
    # 10h. ключ started_at
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "started_at": now_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("started_at свежий -> ok", ok, msg)
    # 10i. свежее, но состояние error -> проблема
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "error", "updated_at": now_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("error состояние -> fail", not ok, msg)
    # 10j. намеренный stopped (свежий) -> ok, не ложная тревога
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "stopped", "updated_at": now_iso}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("stopped -> ok", ok, msg)
    # 10k. нет таймстемпа -> ok (defensive: нельзя судить о возрасте)
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running"}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("без ts -> ok", ok, msg)
    # 10l. битый таймстемп -> ok (defensive)
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "updated_at": "garbage"}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("битый ts -> ok", ok, msg)
    # 10m. будущий таймстемп -> ok (не negative-age fail)
    fut = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    with open(sp, "w") as fh:
        json.dump({"gateway_state": "running", "updated_at": fut}, fh)
    ok, msg = g.check_state_file(sp, "state")
    check("будущий ts -> ok", ok, msg)

print("== 11. cmd_status: интеграция gateway_state.json ==")
with tempfile.TemporaryDirectory() as tmp:
    orig_pid, orig_lock, orig_state = g.PID_FILE, g.LOCK_FILE, g.STATE_FILE
    g.PID_FILE, g.LOCK_FILE = os.path.join(tmp, "p.json"), os.path.join(tmp, "l.json")
    g.STATE_FILE = os.path.join(tmp, "state.json")
    try:
        # свежий state + всё чисто -> rc=0
        with open(g.STATE_FILE, "w") as fh:
            json.dump({"gateway_state": "running", "updated_at": now_iso}, fh)
        with patch.object(g, "scan_gateway_processes", return_value=[]):
            rc = g.cmd_status(None)
        check("свежий state -> rc=0", rc == 0, str(rc))
        # битый state -> rc=1
        with open(g.STATE_FILE, "w") as fh:
            fh.write("{broken")
        with patch.object(g, "scan_gateway_processes", return_value=[]):
            rc = g.cmd_status(None)
        check("битый state -> rc=1", rc == 1, str(rc))
        # протухший state -> rc=1
        with open(g.STATE_FILE, "w") as fh:
            json.dump({"gateway_state": "running", "updated_at": old_iso}, fh)
        with patch.object(g, "scan_gateway_processes", return_value=[]):
            rc = g.cmd_status(None)
        check("stale state -> rc=1", rc == 1, str(rc))
    finally:
        g.PID_FILE, g.LOCK_FILE, g.STATE_FILE = orig_pid, orig_lock, orig_state

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
