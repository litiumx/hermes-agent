#!/usr/bin/env python3
"""agi_gateway_guard.py — защитник gateway от двойных запусков и stale-локов.

Реальная проблема (error_pattern_learner, 02.08.2026):
- gateway_already_running 2220x в logs/errors.log: повторные `hermes gateway run`
  БЕЗ --replace при живом инстансе (31.07, PID 2577566)
- gateway.pid / gateway.lock — JSON с ключом "pid" внутри, НЕ plain PID

Что делает:
  status        — проверяет gateway.pid/gateway.lock: жив ли PID, совпадает ли argv
  scan          — ищет ВСЕ процессы hermes gateway в /proc, находит дубли
  clean-stale   — удаляет stale lock/pid (PID мёртв), делает .bak перед удалением
  self-test     — синтетическая проверка всех веток во временной директории

Exit codes: 0 = ок, 1 = stale/дубли найдены, 2 = ошибка выполнения.

Ничего не конфигурирует, не трогает systemd и живые процессы — только читает
и чистит файлы состояния по явному флагу.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
PID_FILE = os.path.join(HERMES_HOME, "gateway.pid")
LOCK_FILE = os.path.join(HERMES_HOME, "gateway.lock")
STATE_FILE = os.path.join(HERMES_HOME, "gateway_state.json")

GATEWAY_MARKERS = ("hermes", "gateway")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: str):
    """Читает JSON-файл состояния; None если нет/битый."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        return {"_error": str(exc), "_path": path}


def pid_alive(pid: int) -> bool:
    """Жив ли PID (без убийства, только проверка)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def proc_cmdline(pid: int):
    """cmdline процесса из /proc; None если недоступен."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        return raw or None
    except (OSError, FileNotFoundError):
        return None


def is_gateway_cmdline(cmdline: str) -> bool:
    """Похоже ли на процесс hermes gateway (содержит hermes И gateway)."""
    if not cmdline:
        return False
    low = cmdline.lower()
    return all(m in low for m in GATEWAY_MARKERS) and "gateway" in low.split()


def parse_pid_field(data: dict):
    """Извлекает pid из JSON-состояния: прямое поле или вложенное "pid"."""
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, dict):
        pid = pid.get("pid")
    try:
        return int(pid) if pid is not None else None
    except (TypeError, ValueError):
        return None


def check_file_state(path: str, label: str):
    """Оценивает один файл состояния. Возвращает (ok, details)."""
    data = load_json(path)
    if data is None:
        return True, f"{label}: отсутствует (норма)"
    if isinstance(data, dict) and "_error" in data:
        return False, f"{label}: БИТЫЙ JSON ({data['_error']})"
    pid = parse_pid_field(data)
    if pid is None:
        return False, f"{label}: нет поля pid в JSON"
    alive = pid_alive(pid)
    cmdline = proc_cmdline(pid) if alive else None
    if alive and cmdline and is_gateway_cmdline(cmdline):
        return True, f"{label}: PID {pid} жив, argv совпадает"
    if alive and cmdline:
        return False, f"{label}: PID {pid} жив, но это ДРУГОЙ процесс: {cmdline[:80]}"
    if alive:
        return False, f"{label}: PID {pid} жив, cmdline недоступен (не наш?)"
    return False, f"{label}: STALE — PID {pid} мёртв"


def scan_gateway_processes():
    """Все процессы hermes gateway в /proc."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        cmdline = proc_cmdline(pid)
        if is_gateway_cmdline(cmdline or ""):
            try:
                start = os.stat(f"/proc/{pid}").st_mtime
            except OSError:
                start = 0.0
            found.append({"pid": pid, "cmdline": cmdline, "start": start})
    found.sort(key=lambda p: p["start"])
    return found


def cmd_status(args) -> int:
    problems = 0
    print(f"[gateway-guard] {now_iso()} HERMES_HOME={HERMES_HOME}")
    for path, label in ((PID_FILE, "gateway.pid"), (LOCK_FILE, "gateway.lock")):
        ok, msg = check_file_state(path, label)
        print(f"  {'OK ' if ok else 'FAIL'} {msg}")
        problems += 0 if ok else 1
    procs = scan_gateway_processes()
    if procs:
        print(f"  INFO живых gateway-процессов: {len(procs)}")
        for p in procs:
            print(f"    PID {p['pid']} (start {datetime.fromtimestamp(p['start']).isoformat()}) {p['cmdline'][:90]}")
        if len(procs) > 1:
            print(f"  FAIL ДУБЛИ: {len(procs)} gateway-процесса одновременно")
            problems += 1
    else:
        print("  WARN живых gateway-процессов НЕТ (gateway не запущен?)")
    state = load_json(STATE_FILE)
    if isinstance(state, dict) and state.get("gateway_state"):
        print(f"  INFO gateway_state.json: {state.get('gateway_state')}")
    return 1 if problems else 0


def cmd_scan(args) -> int:
    procs = scan_gateway_processes()
    if not procs:
        print("gateway-процессов не найдено")
        return 0
    for p in procs:
        print(f"{p['pid']}\t{datetime.fromtimestamp(p['start']).isoformat()}\t{p['cmdline']}")
    if len(procs) > 1:
        print(f"WARNING: {len(procs)} инстанса — оставить старейший (PID {procs[0]['pid']}), "
              f"остальные 'hermes gateway stop' или kill")
        return 1
    return 0


def cmd_clean_stale(args) -> int:
    """Удаляет stale файлы состояния: PID мёртв ИЛИ битый JSON. С бэкапом."""
    cleaned = 0
    for path, label in ((PID_FILE, "gateway.pid"), (LOCK_FILE, "gateway.lock")):
        data = load_json(path)
        if data is None:
            continue
        if isinstance(data, dict) and "_error" in data:
            stale = True
            reason = "битый JSON"
        else:
            pid = parse_pid_field(data)
            if pid is None:
                stale, reason = True, "нет pid"
            elif not pid_alive(pid):
                stale, reason = True, f"PID {pid} мёртв"
            else:
                # PID жив: проверяем, что это реально gateway (PID-reuse guard)
                cmdline = proc_cmdline(pid)
                if cmdline is None:
                    stale, reason = False, f"PID {pid} жив, cmdline недоступен (KEPT)"
                elif is_gateway_cmdline(cmdline):
                    stale, reason = False, f"PID {pid} жив, argv совпадает"
                else:
                    stale, reason = True, f"PID {pid} жив, но это ДРУГОЙ процесс (PID-reuse): {cmdline[:60]}"
        if stale:
            backup = f"{path}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(path, backup)
            os.remove(path)
            print(f"  REMOVED {label} ({reason}), бэкап: {backup}")
            cleaned += 1
        else:
            print(f"  KEPT {label}: PID жив")
    print(f"clean-stale: удалено файлов: {cleaned}")
    return 0 if cleaned == 0 else 1


def cmd_self_test(args) -> int:
    """Синтетический тест всех веток во временной директории (реальное не трогает)."""
    from unittest.mock import patch

    failures = []
    with tempfile.TemporaryDirectory(prefix="agi_gateway_guard_test_") as tmp:
        alive_pid = os.getpid()
        dead_pid = 99999999
        # мок proc_cmdline: для alive_pid подставляем gateway-подобный argv
        orig_proc_cmdline = proc_cmdline
        fake_cmdline = lambda pid: "/venv/bin/hermes gateway run --replace" if pid == alive_pid else orig_proc_cmdline(pid)
        with patch("__main__.proc_cmdline", side_effect=fake_cmdline):
            # 1. живой корректный lock (PID жив + argv gateway) -> OK
            good = {"pid": alive_pid, "kind": "hermes-gateway", "argv": ["hermes", "gateway", "run"]}
            with open(os.path.join(tmp, "good.json"), "w") as fh:
                json.dump(good, fh)
            ok, msg = check_file_state(os.path.join(tmp, "good.json"), "good")
            if not ok:
                failures.append(f"живой lock распознан как проблему: {msg}")
            # 1b. PID жив, но cmdline НЕ gateway (PID reuse) -> проблема
            foreign_pid = os.getppid() if os.getppid() != alive_pid else 1
            other = {"pid": foreign_pid, "kind": "hermes-gateway"}
            with open(os.path.join(tmp, "other.json"), "w") as fh:
                json.dump(other, fh)
            ok, msg = check_file_state(os.path.join(tmp, "other.json"), "other")
            if ok:
                failures.append(f"PID reuse не распознан: {msg}")
        # 2. stale lock (мёртвый pid)
        stale = {"pid": dead_pid, "kind": "hermes-gateway"}
        with open(os.path.join(tmp, "stale.json"), "w") as fh:
            json.dump(stale, fh)
        ok, msg = check_file_state(os.path.join(tmp, "stale.json"), "stale")
        if ok:
            failures.append(f"stale lock не распознан: {msg}")
        # 3. битый JSON
        with open(os.path.join(tmp, "broken.json"), "w") as fh:
            fh.write("{not json!!")
        ok, msg = check_file_state(os.path.join(tmp, "broken.json"), "broken")
        if ok:
            failures.append(f"битый JSON не распознан: {msg}")
        # 4. отсутствующий файл
        ok, msg = check_file_state(os.path.join(tmp, "missing.json"), "missing")
        if not ok:
            failures.append(f"отсутствующий файл распознан как проблему: {msg}")
        # 5. pid_alive
        if not pid_alive(alive_pid) or pid_alive(dead_pid):
            failures.append("pid_alive работает неверно")
        # 6. is_gateway_cmdline
        if not is_gateway_cmdline("/venv/bin/hermes gateway run --replace"):
            failures.append("is_gateway_cmdline не распознал gateway")
        if is_gateway_cmdline("/usr/bin/python3 server.py"):
            failures.append("is_gateway_cmdline ложное срабатывание")

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST OK: 6/6 веток пройдено (good/stale/broken/missing/alive/cmdline)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Защитник Hermes gateway от двойных запусков и stale-локов")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="проверить gateway.pid/gateway.lock и живые процессы")
    sub.add_parser("scan", help="список всех gateway-процессов, выявить дубли")
    sub.add_parser("clean-stale", help="удалить stale lock/pid (PID мёртв), с .bak")
    sub.add_parser("self-test", help="синтетический тест всех веток")
    args = parser.parse_args()

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "clean-stale":
        return cmd_clean_stale(args)
    if args.cmd == "self-test":
        return cmd_self_test(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
