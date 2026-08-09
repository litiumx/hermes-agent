#!/usr/bin/env python3
"""agi_test_queue_paths.py — env-пути и живой цикл agi_self_directed_queue.py.

Цикл 8 (09.08): планировщик "не запускался вживую" — report() падал с
PermissionError в песочнице/контейнере: 4 файловых пути и TASK_ACTIONS
захардкожены на /root/.hermes. Фикс: env-оверрайды AGI_BRIDGE_FILE /
AGI_PATTERNS_FILE / AGI_KNOWLEDGE_FILE / AGI_QUEUE_FILE / AGI_SCRIPTS_DIR /
HERMES_HOME. Тесты: subprocess end-to-end (report/next/run-next), привязка
констант к env, fallback на дефолты, живой цикл run_next (done/failed/error).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
MODULE = SCRIPTS_DIR / "agi_self_directed_queue.py"

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

def json_load(path):
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None

def clean_env():
    e = dict(os.environ)
    for k in ("AGI_BRIDGE_FILE", "AGI_PATTERNS_FILE", "AGI_KNOWLEDGE_FILE",
              "AGI_QUEUE_FILE", "AGI_SCRIPTS_DIR", "HERMES_HOME"):
        e.pop(k, None)
    return e

def run_cli(env, *args):
    """Модуль как CLI в подпроцессе. (rc, stdout, stderr)"""
    p = subprocess.run([sys.executable, str(MODULE), *args],
                       capture_output=True, text=True, env=env, timeout=60,
                       cwd=str(SCRIPTS_DIR))
    return p.returncode, p.stdout, p.stderr

def run_py(env, code):
    """python3 -c с env в каталоге scripts. (rc, stdout, stderr)"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=60, cwd=str(SCRIPTS_DIR))
    return p.returncode, p.stdout, p.stderr

print("== 1. Дефолты без env (регрессия: /root/.hermes) ==")
rc, out, err = run_py(clean_env(),
    "import sys; sys.path.insert(0, '.'); import agi_self_directed_queue as q;"
    " print(q.QUEUE_FILE); print(q.BRIDGE_FILE); print(q.TASK_ACTIONS[0][1][-1])")
check("дефолт QUEUE_FILE=/root/.hermes", rc == 0 and "/root/.hermes/data/task_queue.json" in out, out + err)
check("дефолт BRIDGE_FILE=/root/.hermes", "/root/.hermes/session/bridge.json" in out, out)
check("дефолт scripts в TASK_ACTIONS", "/root/.hermes/scripts/proactive_scan.py" in out, out)

print("== 2. AGI_QUEUE_FILE / AGI_SCRIPTS_DIR переопределяют константы ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_QUEUE_FILE"] = os.path.join(tmp, "q.json")
    env["AGI_SCRIPTS_DIR"] = os.path.join(tmp, "bin")
    rc, out, err = run_py(env,
        "import sys; sys.path.insert(0, '.'); import agi_self_directed_queue as q;"
        " print(q.QUEUE_FILE); print(q.TASK_ACTIONS[0][1][-1])")
    check("AGI_QUEUE_FILE применён", rc == 0 and env["AGI_QUEUE_FILE"] in out, out + err)
    check("AGI_SCRIPTS_DIR применён", env["AGI_SCRIPTS_DIR"] + "/proactive_scan.py" in out, out)

print("== 3. HERMES_HOME даёт session/data подкаталоги ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env,
        "import sys; sys.path.insert(0, '.'); import agi_self_directed_queue as q;"
        " print(q.QUEUE_FILE); print(q.BRIDGE_FILE); print(q.PATTERNS_FILE)")
    check("HERMES_HOME → data/task_queue.json", rc == 0 and out.strip().splitlines()[0].startswith(tmp + "/data/task_queue.json"), out + err)
    check("HERMES_HOME → session/bridge.json", out.strip().splitlines()[1].startswith(tmp + "/session/bridge.json"), out)

print("== 4. Живой цикл: report/next с env не падают (был PermissionError) ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_QUEUE_FILE"] = os.path.join(tmp, "task_queue.json")
    env["AGI_BRIDGE_FILE"] = os.path.join(tmp, "bridge.json")
    env["AGI_PATTERNS_FILE"] = os.path.join(tmp, "patterns.json")
    env["AGI_KNOWLEDGE_FILE"] = os.path.join(tmp, "knowledge.json")
    rc, out, err = run_cli(env, "report")
    check("report rc=0", rc == 0, f"rc={rc} {err[-200:]}")
    check("queue файл создан", os.path.exists(env["AGI_QUEUE_FILE"]))
    rc, out, err = run_cli(env, "next")
    check("next rc=0", rc == 0, f"rc={rc} {err[-200:]}")
    check("next печатает NEXT", "NEXT:" in out, out)

print("== 5. run-next: живой цикл с фейковыми скриптами ==")
with tempfile.TemporaryDirectory() as tmp:
    bin_dir = Path(tmp) / "bin"
    bin_dir.mkdir()
    ok_script = bin_dir / "proactive_scan.py"
    ok_script.write_text("print('fake-scan: ok')\n")
    bad_script = bin_dir / "self_improve.py"
    bad_script.write_text("import sys; sys.exit(1)\n")
    env = clean_env()
    env["AGI_QUEUE_FILE"] = os.path.join(tmp, "task_queue.json")
    env["AGI_BRIDGE_FILE"] = os.path.join(tmp, "bridge.json")
    env["AGI_PATTERNS_FILE"] = os.path.join(tmp, "patterns.json")
    env["AGI_KNOWLEDGE_FILE"] = os.path.join(tmp, "knowledge.json")
    env["AGI_SCRIPTS_DIR"] = str(bin_dir)
    # 5a. первый run-next исполняет health check (скрипт ok) → done
    rc, out, err = run_cli(env, "run-next")
    check("run-next rc=0", rc == 0, f"rc={rc} {err[-200:]}")
    check("status done", "[done]" in out, out)
    check("exit_code 0", "exit_code: 0" in out, out)
    data = json_load(env["AGI_QUEUE_FILE"])
    check("history записана", data and len(data.get("history", [])) == 1, str(data)[:200])
    check("task ушла из очереди", data and all(t["task"] != "Run system health check and proactive scan" for t in data["queue"]), str(data)[:200])
    # 5b. второй run-next: health в кулдауне → self_improve (скрипт exit 1) → failed
    rc, out, err = run_cli(env, "run-next")
    check("второй run-next rc=0", rc == 0, f"rc={rc} {err[-200:]}")
    check("status failed", "[failed]" in out, out)
    check("exit_code 1", "exit_code: 1" in out, out)
    data = json_load(env["AGI_QUEUE_FILE"])
    check("history 2 записи", data and len(data.get("history", [])) == 2, str(data)[:200])

print("== 6. run-next: отсутствующий скрипт → честный error, не краш ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_QUEUE_FILE"] = os.path.join(tmp, "task_queue.json")
    env["AGI_BRIDGE_FILE"] = os.path.join(tmp, "bridge.json")
    env["AGI_PATTERNS_FILE"] = os.path.join(tmp, "patterns.json")
    env["AGI_KNOWLEDGE_FILE"] = os.path.join(tmp, "knowledge.json")
    env["AGI_SCRIPTS_DIR"] = os.path.join(tmp, "empty_bin")  # не существует
    rc, out, err = run_cli(env, "run-next")
    check("rc=0 (обработано)", rc == 0, f"rc={rc} {err[-200:]}")
    check("status error", "[error]" in out, out)
    data = json_load(env["AGI_QUEUE_FILE"])
    check("история с reason", data and data["history"][-1].get("reason"), str(data)[:200])

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
