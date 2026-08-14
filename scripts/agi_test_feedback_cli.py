#!/usr/bin/env python3
"""Юнит-тест CLI feedback_companion + авто-фидбек run_next (цикл 28).

Grow point (цикл 27): feedback_companion существует как функция, но у
learner'а НЕТ CLI-входа (только update/report) — оператор не может
подтвердить/опровергнуть предсказание руками, а run_next исполняет
companion-задачи и молча удаляет их из очереди БЕЗ обратной связи:
learner не узнаёт, подтвердился ли предсказанный паттерн в выводе.

Этот тест проверяет:
- CLI: feedback <pattern> <confirmed> [--module M] [--boost B] [--penalty P]
  → JSON-отчёт, exit 0 при успехе
- CLI: confirmed=True усиливает пары, False ослабляет
- CLI: --module правит module_cooccurrences, global не трогает
- CLI: --boost/--penalty кастомные значения
- CLI: невалидный confirmed / нет аргументов / неизвестный паттерн → exit 1
- run_next: companion-задача, паттерн ЕСТЬ в выводе → авто-фидбек confirmed=True
- run_next: companion-задача, паттерна НЕТ в выводе → авто-фидбек confirmed=False
- run_next: companion-задача с module → авто-фидбек в module-карту
- run_next: не-companion задача → без ключа feedback в результате
- run_next: timeout → без фидбека (нет вывода для детекта)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_error_pattern_learner as l
import agi_self_directed_queue as q

LEARNER = "/home/sandbox/hermes-agent/scripts/agi_error_pattern_learner.py"
TMP = Path(tempfile.mkdtemp())
SHARED = TMP / "error_patterns.json"
l.PATTERNS_FILE = SHARED
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = SHARED
q.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"
q.QUEUE_FILE = TMP / "task_queue.json"

TMP_BIN = TMP / "bin"
TMP_BIN.mkdir(exist_ok=True)
ECHO = TMP_BIN / "echo_pattern.py"
ECHO.write_text("print('gateway timeout observed')\n")
SILENT = TMP_BIN / "silent.py"
SILENT.write_text("print('all clean')\n")
SLOW = TMP_BIN / "slow.py"
SLOW.write_text("import time; time.sleep(30)\n")


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


def warm_defaults():
    now = time.time()
    write(q.QUEUE_FILE, {"history": [
        {"task": "Run system health check and proactive scan", "ts": now - 60},
        {"task": "Run self-improvement cycle (self_improve.py)", "ts": now - 60},
    ]})


def companion_data(pattern="gateway timeout", weight=2.5):
    """weight=2.5: после penalty 0.5 остаётся 2.0 ≥ min_pairs — пара выживает."""
    return {
        "history": [], "streaks": {}, "learned_patterns": [], "last_update": 0,
        "cooccurrences": {pattern: {"A": weight}, "A": {pattern: weight}},
        "companions": [{"pattern": pattern, "co_score": 4.0, "anchors": ["A"]}],
        "module_cooccurrences": {},
        "module_companions": [],
    }


def run_cli(*args):
    env = {**os.environ, "AGI_PATTERNS_FILE": str(SHARED)}
    return subprocess.run(
        [sys.executable, LEARNER, *args], capture_output=True, text=True, env=env
    )


# --- Test 1: CLI feedback <pattern> true → exit 0, пары усилены ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback", "gateway timeout", "true")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("confirmed") is True, rep
assert rep.get("adjusted_pairs") == 1, rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["cooccurrences"]["A"]["gateway timeout"] == 3.5, out["cooccurrences"]
assert out["feedback"][-1]["confirmed"] is True
print("TEST 1 PASS: CLI feedback true → exit 0, пары 2.5→3.5")

# --- Test 2: CLI feedback <pattern> false → пары ослаблены ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback", "gateway timeout", "false")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("confirmed") is False, rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["cooccurrences"]["A"]["gateway timeout"] == 2.0, out["cooccurrences"]
print("TEST 2 PASS: CLI feedback false → пары 2.5→2.0 (выжили ≥ min_pairs)")

# --- Test 3: CLI --module правит module-карту, global не тронут ---
clean()
data = companion_data()
data["module_cooccurrences"] = {
    "gateway.log": {"gateway timeout": {"A": 2.0}, "A": {"gateway timeout": 2.0}}}
data["module_companions"] = [
    {"pattern": "gateway timeout", "source": "gateway.log",
     "co_score": 3.5, "anchors": ["A"]}]
write(l.PATTERNS_FILE, data)
proc = run_cli("feedback", "gateway timeout", "true", "--module", "gateway.log")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("module") == "gateway.log", rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["module_cooccurrences"]["gateway.log"]["A"]["gateway timeout"] == 3.0
assert out["cooccurrences"]["A"]["gateway timeout"] == 2.5, "global не должен меняться"
print("TEST 3 PASS: CLI --module правит module-карту, global не тронут")

# --- Test 4: CLI --boost кастомное значение ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback", "gateway timeout", "true", "--boost", "2.5")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["cooccurrences"]["A"]["gateway timeout"] == 5.0, out["cooccurrences"]
print("TEST 4 PASS: CLI --boost 2.5 → пары 2.5→5.0")

# --- Test 5: CLI невалидный confirmed → exit 1 ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback", "gateway timeout", "maybe")
assert proc.returncode != 0, "ожидался exit 1"
print("TEST 5 PASS: CLI невалидный confirmed → exit 1")

# --- Test 6: CLI без аргументов → exit 1 ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback")
assert proc.returncode != 0, "ожидался exit 1"
print("TEST 6 PASS: CLI без аргументов → exit 1")

# --- Test 7: CLI неизвестный паттерн → exit 1, error в отчёте ---
clean()
write(l.PATTERNS_FILE, companion_data())
proc = run_cli("feedback", "never seen", "true")
assert proc.returncode != 0, "ожидался exit 1"
rep = json.loads(proc.stdout)
assert "error" in rep, rep
print("TEST 7 PASS: CLI неизвестный паттерн → exit 1 + error")

# --- Test 8: run_next авто-фидбек — паттерн в выводе → confirmed=True ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, companion_data())
q.TASK_ACTIONS = [  # companion-задача "Investigate and fix pattern: ..."
    (("pattern",), ["python3", str(ECHO)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert result.get("feedback_confirmed") is True, result
assert result.get("feedback", {}).get("confirmed") is True, result
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["cooccurrences"]["A"]["gateway timeout"] == 3.5, out["cooccurrences"]
print("TEST 8 PASS: run_next авто-фидбек confirmed=True (паттерн в выводе)")

# --- Test 9: run_next авто-фидбек — паттерна нет в выводе → confirmed=False ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, companion_data())
q.TASK_ACTIONS = [
    (("pattern",), ["python3", str(SILENT)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert result.get("feedback_confirmed") is False, result
assert result.get("feedback", {}).get("confirmed") is False, result
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["cooccurrences"]["A"]["gateway timeout"] == 2.0, out["cooccurrences"]
print("TEST 9 PASS: run_next авто-фидбек confirmed=False (паттерна нет)")

# --- Test 10: run_next companion с module → авто-фидбек в module-карту ---
clean()
warm_defaults()
data = companion_data()
data["companions"] = []  # только module-companion, иначе global 4.0 > module 3.5
data["module_cooccurrences"] = {
    "gateway.log": {"gateway timeout": {"A": 2.5}, "A": {"gateway timeout": 2.5}}}
data["module_companions"] = [
    {"pattern": "gateway timeout", "source": "gateway.log",
     "co_score": 3.5, "anchors": ["A"]}]
write(l.PATTERNS_FILE, data)
q.TASK_ACTIONS = [
    (("pattern",), ["python3", str(ECHO)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert result.get("feedback", {}).get("module") == "gateway.log", result
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["module_cooccurrences"]["gateway.log"]["A"]["gateway timeout"] == 3.5
assert out["cooccurrences"]["A"]["gateway timeout"] == 2.5, \
    "global не должен измениться"
print("TEST 10 PASS: run_next module-фидбек → module-карта, global цел")

# --- Test 11: run_next не-companion задача → без ключа feedback ---
clean()
warm_defaults()
# Без companions: только knowledge_gap задача в очереди
write(l.PATTERNS_FILE, {"history": [], "streaks": {}, "learned_patterns": [],
                        "last_update": 0, "cooccurrences": {},
                        "companions": [], "module_cooccurrences": {},
                        "module_companions": []})
# knowledge_gap задача (source=knowledge_gap): generic research-цикл
write(q.KNOWLEDGE_FILE, {"last_search": time.time() - 10 * 3600, "findings": []})
q.TASK_ACTIONS = [
    (("research cycle",), ["python3", str(SILENT)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert "feedback" not in result, result
assert "feedback_confirmed" not in result, result
print("TEST 11 PASS: run_next не-companion → без фидбека")

# --- Test 12: run_next timeout → без фидбека (нет вывода) ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, companion_data())
q.TASK_ACTIONS = [
    (("pattern",), ["python3", str(SLOW)]),
]
old_timeout = q.TASK_TIMEOUT
q.TASK_TIMEOUT = 2
try:
    result = q.run_next()
finally:
    q.TASK_TIMEOUT = old_timeout
assert result is not None and result.get("status") == "timeout", result
assert "feedback" not in result, result
print("TEST 12 PASS: run_next timeout → без фидбека")

print("\nALL TESTS PASSED")
