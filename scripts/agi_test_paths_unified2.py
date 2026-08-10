#!/usr/bin/env python3
"""agi_test_paths_unified2.py — унификация env-путей: focus_agent/config_guard/
mcp_keepalive/context_store (цикл 10, 10.08.2026).

Циклы 8-9 унифицировали queue/session_bridge/error_pattern_learner/
curious_agent (HERMES_HOME + AGI_*_FILE). Остались хардкоды /root/.hermes:
- agi_focus_agent.py   (HERMES_HOME константа, KB/HISTORY/state.db)
- agi_config_guard.py  (ROOT, CONFIG_FILE, PATTERNS_FILE, SCAN_DIRS)
- agi_mcp_keepalive.py (LOGS_DIR, ERRORS_LOG, STATE_FILE)
- agi_context_store.py (AGI_CONTEXT_STORE_DB есть, но HERMES_HOME игнорируется)

Тесты: дефолты без env (регрессия), HERMES_HOME → подкаталоги, точные
AGI_*_FILE оверрайды, живой цикл в tempdir (песочница без прав на /root).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

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


def clean_env():
    e = dict(os.environ)
    for k in ("HERMES_HOME", "AGI_KB_FILE", "AGI_HISTORY_FILE",
              "AGI_SESSION_STATE", "AGI_CONFIG_FILE", "AGI_PATTERNS_FILE",
              "AGI_LOG_DIR", "AGI_ERRORS_LOG", "AGI_MCP_STATE_FILE",
              "AGI_CONTEXT_STORE_DB"):
        e.pop(k, None)
    return e


def run_py(env, code):
    """python3 -c с env в каталоге scripts. (rc, stdout, stderr)"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=60, cwd=str(SCRIPTS_DIR))
    return p.returncode, p.stdout, p.stderr


FA = ("import sys; sys.path.insert(0, '.'); import agi_focus_agent as f;"
      " print(f.HERMES_HOME); print(f.KB_FILE); print(f.HISTORY_FILE);"
      " print(f.SESSION_STATE)")
CG = ("import sys; sys.path.insert(0, '.'); import agi_config_guard as g;"
      " print(g.ROOT); print(g.CONFIG_FILE); print(g.PATTERNS_FILE);"
      " print(g.SCAN_DIRS)")
MK = ("import sys; sys.path.insert(0, '.'); import agi_mcp_keepalive as m;"
      " print(m.LOGS_DIR); print(m.ERRORS_LOG); print(m.STATE_FILE)")
CS = ("import sys; sys.path.insert(0, '.'); import agi_context_store as s;"
      " print(s.DB_PATH)")

print("== 1. focus_agent: дефолты без env (регрессия /root/.hermes) ==")
rc, out, err = run_py(clean_env(), FA)
check("дефолт 4 пути на /root/.hermes",
      rc == 0 and "/root/.hermes/data/knowledge_block.json" in out and
      "/root/.hermes/data/focus_history.json" in out and
      "/root/.hermes/state.db" in out, out + err)

print("== 2. focus_agent: HERMES_HOME + точные оверрайды ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, FA)
    check("HERMES_HOME → data/ и state.db",
          rc == 0 and f"{tmp}/data/knowledge_block.json" in out and
          f"{tmp}/data/focus_history.json" in out and
          f"{tmp}/state.db" in out, out + err)
    env2 = clean_env()
    env2["AGI_KB_FILE"] = os.path.join(tmp, "kb.json")
    env2["AGI_HISTORY_FILE"] = os.path.join(tmp, "hist.json")
    env2["AGI_SESSION_STATE"] = os.path.join(tmp, "s.db")
    rc, out, err = run_py(env2, FA)
    check("AGI_KB_FILE/AGI_HISTORY_FILE/AGI_SESSION_STATE применены",
          rc == 0 and f"{tmp}/kb.json" in out and f"{tmp}/hist.json" in out and
          f"{tmp}/s.db" in out, out + err)

print("== 3. config_guard: дефолты без env (регрессия) ==")
rc, out, err = run_py(clean_env(), CG)
check("дефолт ROOT/CONFIG_FILE/PATTERNS_FILE/SCAN_DIRS",
      rc == 0 and "/root/.hermes/config.yaml" in out and
      "/root/.hermes/data/error_patterns.json" in out and
      "'/root/.hermes/data'" in out, out + err)

print("== 4. config_guard: HERMES_HOME + точные оверрайды ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, CG)
    check("HERMES_HOME → config.yaml/data/SCAN_DIRS",
          rc == 0 and f"{tmp}/config.yaml" in out and
          f"{tmp}/data/error_patterns.json" in out and
          f"'{tmp}/data'" in out, out + err)
    env2 = clean_env()
    env2["AGI_CONFIG_FILE"] = os.path.join(tmp, "c.yaml")
    env2["AGI_PATTERNS_FILE"] = os.path.join(tmp, "p.json")
    rc, out, err = run_py(env2, CG)
    check("AGI_CONFIG_FILE/AGI_PATTERNS_FILE применены",
          rc == 0 and f"{tmp}/c.yaml" in out and f"{tmp}/p.json" in out,
          out + err)

print("== 5. mcp_keepalive: дефолты без env (регрессия) ==")
rc, out, err = run_py(clean_env(), MK)
check("дефолт LOGS_DIR/ERRORS_LOG/STATE_FILE",
      rc == 0 and "/root/.hermes/logs" in out and
      "/root/.hermes/logs/errors.log" in out and
      "/root/.hermes/data/mcp_keepalive.json" in out, out + err)

print("== 6. mcp_keepalive: HERMES_HOME + точные оверрайды ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, MK)
    check("HERMES_HOME → logs/ и data/",
          rc == 0 and f"{tmp}/logs" in out and
          f"{tmp}/logs/errors.log" in out and
          f"{tmp}/data/mcp_keepalive.json" in out, out + err)
    env2 = clean_env()
    env2["AGI_LOG_DIR"] = os.path.join(tmp, "lg")
    env2["AGI_ERRORS_LOG"] = os.path.join(tmp, "e.log")
    env2["AGI_MCP_STATE_FILE"] = os.path.join(tmp, "mcp.json")
    rc, out, err = run_py(env2, MK)
    check("AGI_LOG_DIR/AGI_ERRORS_LOG/AGI_MCP_STATE_FILE применены",
          rc == 0 and f"{tmp}/lg" in out and f"{tmp}/e.log" in out and
          f"{tmp}/mcp.json" in out, out + err)

print("== 7. context_store: дефолт и HERMES_HOME ==")
rc, out, err = run_py(clean_env(), CS)
check("дефолт DB_PATH=/root/.hermes/data/context_store.db",
      rc == 0 and "/root/.hermes/data/context_store.db" in out, out + err)
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, CS)
    check("HERMES_HOME → data/context_store.db",
          rc == 0 and f"{tmp}/data/context_store.db" in out, out + err)
    env2 = clean_env()
    env2["AGI_CONTEXT_STORE_DB"] = os.path.join(tmp, "c.db")
    rc, out, err = run_py(env2, CS)
    check("AGI_CONTEXT_STORE_DB применён",
          rc == 0 and f"{tmp}/c.db" in out, out + err)

print("== 8. Живой цикл в tempdir (без прав на /root/.hermes) ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    code = ("import sys; sys.path.insert(0, '.');"
            " import agi_context_store as s;"
            " s.save_context({'last_task': 't10', 'active_projects': ['agi']});"
            " c = s.load_context();"
            " print('OK' if c.get('last_task') == 't10' else c)")
    rc, out, err = run_py(env, code)
    check("context_store save/load в tempdir", rc == 0 and "OK" in out,
          out + err)
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_MCP_STATE_FILE"] = os.path.join(tmp, "mcp.json")
    code = ("import sys; sys.path.insert(0, '.');"
            " import agi_mcp_keepalive as m;"
            " m.save_state({'servers': {}});"
            " d = m.load_state();"
            " print('OK' if isinstance(d, dict) and 'servers' in d else d)")
    rc, out, err = run_py(env, code)
    check("mcp_keepalive save/load_state в tempdir", rc == 0 and "OK" in out,
          out + err)
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    code = ("import sys; sys.path.insert(0, '.');"
            " import agi_focus_agent as f;"
            " f.add_knowledge('t10', 'тест');"
            " kb = f.load_kb();"
            " print('OK' if any(e.get('topic') == 't10' for e in kb.get('knowledge', [])) else kb)")
    rc, out, err = run_py(env, code)
    check("focus_agent add_knowledge/load_kb в tempdir",
          rc == 0 and "OK" in out, out + err)
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, "import subprocess, sys; sys.exit(0)")
    p = subprocess.run([sys.executable, "agi_config_guard.py", "--json"],
                       capture_output=True, text=True, env=env, timeout=60,
                       cwd=str(SCRIPTS_DIR))
    check("config_guard --json в tempdir (rc=0, валидный JSON)",
          p.returncode == 0 and p.stdout.strip().startswith("{"),
          f"rc={p.returncode} {p.stdout[:200]} {p.stderr[:200]}")

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
