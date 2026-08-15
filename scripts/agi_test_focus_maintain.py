#!/usr/bin/env python3
"""agi_test_focus_maintain.py — тесты автовызова memory-ретеншна в auto_focus_cycle
(цикл 29). Кулдаун 24ч, включение только через AGI_MAINTAIN_MEMORY=1
(защита от случайного трогания prod-БД в тестах/песочнице)."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# ДО первого вызова auto_focus_cycle: context_store читает env при импорте
TMP = Path(tempfile.mkdtemp(prefix="agi_focus_maint_"))
os.environ["AGI_CONTEXT_STORE_DB"] = str(TMP / "ctx.db")

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
    tmp = Path(tempfile.mkdtemp(prefix="agi_focus_maint_env_"))
    f.KB_FILE = tmp / "knowledge_block.json"
    f.HISTORY_FILE = tmp / "focus_history.json"
    return tmp


def seed_kb():
    f.save_kb({"created": datetime.now().isoformat(),
               "knowledge": [{"topic": "a", "content": "x", "source": "s",
                              "timestamp": datetime.now().isoformat()}]})


print("== 1. Флаг выключен → тишина (нет memory_maintained, нет краха) ==")
make_env()
seed_kb()
os.environ.pop("AGI_MAINTAIN_MEMORY", None)
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("action=none", r["action"] == "none", str(r))
check("нет memory_maintained", "memory_maintained" not in r, str(r))

print("== 2. Флаг включён, нет истории → maintenance выполнен ==")
make_env()
seed_kb()
os.environ["AGI_MAINTAIN_MEMORY"] = "1"
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("memory_maintained присутствует", "memory_maintained" in r, str(r))
check("retain внутри", "retain" in r["memory_maintained"], str(r))
check("consolidate внутри", "consolidate" in r["memory_maintained"], str(r))
check("demoted_medium=0 (пусто)", 
      r["memory_maintained"]["retain"]["demoted_medium"] == 0, str(r))

print("== 3. Свежая maintenance в истории → кулдаун (24ч) ==")
make_env()
seed_kb()
f._log_event({"time": datetime.now().isoformat(), "type": "memory_maintain"})
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("нет memory_maintained (кулдаун)",
      "memory_maintained" not in r, str(r))

print("== 4. Старая maintenance (50ч назад) → выполняется снова ==")
make_env()
seed_kb()
f._log_event({"time": (datetime.now() - timedelta(hours=50)).isoformat(),
              "type": "memory_maintain"})
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("memory_maintained присутствует", "memory_maintained" in r, str(r))

print("== 5. Maintenance + компакция работают вместе ==")
make_env()
seed_kb()
f.get_context_usage = lambda: (700, 900_000)
r = f.auto_focus_cycle()
check("memory_maintained есть", "memory_maintained" in r, str(r))
check("компакция отработала",
      r["action"] in ("compacted", "compact_advised"), str(r["action"]))

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
