#!/usr/bin/env python3
"""agi_test_paths_unified.py — унификация env-путей в agi_* скриптах (цикл 9, 10.08.2026).

Цикл 8 унифицировал пути только в agi_self_directed_queue.py (HERMES_HOME +
AGI_*_FILE). session_bridge/error_pattern_learner/curious_agent остались с
хардкодом /root/.hermes — в песочнице/контейнере (нет записи в /root/.hermes)
они падали с PermissionError. Тесты: дефолты без env (регрессия), HERMES_HOME
→ подкаталоги, точные AGI_*_FILE оверрайды, живой цикл save/load в tempdir.

Имена env консистентны с циклом 8: HERMES_HOME, AGI_BRIDGE_FILE,
AGI_PATTERNS_FILE, AGI_KNOWLEDGE_FILE (+ новые AGI_SESSION_DIR,
AGI_SUPERVISOR_LOG, AGI_LOG_DIR).
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
    for k in ("AGI_BRIDGE_FILE", "AGI_PATTERNS_FILE", "AGI_KNOWLEDGE_FILE",
              "AGI_QUEUE_FILE", "AGI_SCRIPTS_DIR", "AGI_SESSION_DIR",
              "AGI_SUPERVISOR_LOG", "AGI_LOG_DIR", "HERMES_HOME"):
        e.pop(k, None)
    return e


def run_py(env, code):
    """python3 -c с env в каталоге scripts. (rc, stdout, stderr)"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=60, cwd=str(SCRIPTS_DIR))
    return p.returncode, p.stdout, p.stderr


SB = ("import sys; sys.path.insert(0, '.'); import agi_session_bridge as b;"
      " print(b.SESSION_DIR); print(b.BRIDGE_FILE); print(b.HISTORY_DIR)")
EPL = ("import sys; sys.path.insert(0, '.'); import agi_error_pattern_learner as e;"
       " print(e.PATTERNS_FILE); print(e.SUPERVISOR_LOG);"
       " print(e.SESSION_DIR); print(e.LOG_DIR)")
CA = ("import sys; sys.path.insert(0, '.'); import agi_curious_agent as c;"
      " print(c.KNOWLEDGE_FILE); print(c.BRIDGE_FILE)")

print("== 1. session_bridge: дефолты без env (регрессия /root/.hermes) ==")
rc, out, err = run_py(clean_env(), SB)
check("дефолт SESSION_DIR=/root/.hermes/session",
      rc == 0 and "/root/.hermes/session" in out, out + err)
check("дефолт BRIDGE_FILE=/root/.hermes/session/bridge.json",
      "/root/.hermes/session/bridge.json" in out, out)
check("дефолт HISTORY_DIR=/root/.hermes/session/history",
      "/root/.hermes/session/history" in out, out)

print("== 2. session_bridge: HERMES_HOME и AGI_SESSION_DIR ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, SB)
    check("HERMES_HOME → SESSION_DIR/BRIDGE_FILE/HISTORY_DIR",
          rc == 0 and f"{tmp}/session" in out, out + err)
    env2 = clean_env()
    env2["AGI_SESSION_DIR"] = os.path.join(tmp, "custom-session")
    rc, out, err = run_py(env2, SB)
    check("AGI_SESSION_DIR переопределяет session",
          rc == 0 and f"{tmp}/custom-session" in out and
          f"{tmp}/custom-session/bridge.json" in out, out + err)

print("== 3. error_pattern_learner: дефолты без env (регрессия) ==")
rc, out, err = run_py(clean_env(), EPL)
check("дефолт 4 пути на /root/.hermes",
      rc == 0 and "/root/.hermes/data/error_patterns.json" in out and
      "/root/.hermes/SUPERVISOR_LOG.md" in out and
      "/root/.hermes/session" in out and "/root/.hermes/logs" in out,
      out + err)

print("== 4. error_pattern_learner: HERMES_HOME + точные оверрайды ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, EPL)
    check("HERMES_HOME → data/session/logs",
          rc == 0 and f"{tmp}/data/error_patterns.json" in out and
          f"{tmp}/SUPERVISOR_LOG.md" in out and
          f"{tmp}/session" in out and f"{tmp}/logs" in out, out + err)
    env2 = clean_env()
    env2["AGI_PATTERNS_FILE"] = os.path.join(tmp, "p.json")
    env2["AGI_SUPERVISOR_LOG"] = os.path.join(tmp, "sup.md")
    env2["AGI_SESSION_DIR"] = os.path.join(tmp, "s")
    env2["AGI_LOG_DIR"] = os.path.join(tmp, "l")
    rc, out, err = run_py(env2, EPL)
    check("4 точных оверрайда применены",
          rc == 0 and f"{tmp}/p.json" in out and f"{tmp}/sup.md" in out and
          f"{tmp}/s" in out and f"{tmp}/l" in out, out + err)

print("== 5. curious_agent: дефолты без env (регрессия) ==")
rc, out, err = run_py(clean_env(), CA)
check("дефолт KNOWLEDGE_FILE/BRIDGE_FILE",
      rc == 0 and "/root/.hermes/data/curious_knowledge.json" in out and
      "/root/.hermes/session/bridge.json" in out, out + err)

print("== 6. curious_agent: HERMES_HOME + точные оверрайды ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["HERMES_HOME"] = tmp
    rc, out, err = run_py(env, CA)
    check("HERMES_HOME → data/session",
          rc == 0 and f"{tmp}/data/curious_knowledge.json" in out and
          f"{tmp}/session/bridge.json" in out, out + err)
    env2 = clean_env()
    env2["AGI_KNOWLEDGE_FILE"] = os.path.join(tmp, "k.json")
    env2["AGI_BRIDGE_FILE"] = os.path.join(tmp, "b.json")
    rc, out, err = run_py(env2, CA)
    check("AGI_KNOWLEDGE_FILE/AGI_BRIDGE_FILE применены",
          rc == 0 and f"{tmp}/k.json" in out and f"{tmp}/b.json" in out,
          out + err)

print("== 7. Живой цикл в tempdir (без прав на /root/.hermes) ==")
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_SESSION_DIR"] = os.path.join(tmp, "session")
    code = ("import sys; sys.path.insert(0, '.');"
            " import agi_session_bridge as b;"
            " r = b.save_context({'last_task': 't9', 'active_projects': ['x']});"
            " c = b.load_context();"
            " print('OK' if c.get('last_task') == 't9' else c)")
    rc, out, err = run_py(env, code)
    check("session_bridge save/load в tempdir",
          rc == 0 and "OK" in out, out + err)
with tempfile.TemporaryDirectory() as tmp:
    env = clean_env()
    env["AGI_KNOWLEDGE_FILE"] = os.path.join(tmp, "k.json")
    code = ("import sys, json, time; sys.path.insert(0, '.');"
            " import agi_curious_agent as c;"
            " c.save_knowledge({'findings': [], 'topics_searched': [],"
            " 'last_search': time.time()});"
            " d = json.loads(c.KNOWLEDGE_FILE.read_text());"
            " print('OK' if isinstance(d.get('findings'), list) else d)")
    rc, out, err = run_py(env, code)
    check("curious_agent save_knowledge в tempdir",
          rc == 0 and "OK" in out, out + err)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
