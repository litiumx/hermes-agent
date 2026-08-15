#!/usr/bin/env python3
"""Юнит-тест авто-фидбека risk-задач (цикл 30).

Grow point (цикл 29): run_next замыкает петлю обратной связи ТОЛЬКО для
companion-задач (feedback_companion, цикл 26-28). Risk-задачи («Investigate
and fix pattern: ...», source="risk", генерируются из predict_risks при
streak >= 3) исполняются и удаляются из очереди БЕЗ обратной связи:
learner не узнаёт, подтвердился ли риск в выводе расследования. Если
паттерн больше не проявляется, streak остаётся на 3+ и задача
пере-генерируется вечно.

Этот тест проверяет:
- feedback_risk: confirmed=False → streak снижается на penalty (floor 0)
- feedback_risk: confirmed=False → риск выпадает из predict_risks (streak < 3)
- feedback_risk: confirmed=True → streak НЕ трогается (риск подтверждён)
- feedback_risk: неизвестный/пустой паттерн → error, без журнала
- feedback_risk: кастомный penalty
- CLI: feedback-risk <pattern> <confirmed> [--penalty N] → JSON, exit 0/1
- run_next: risk-задача, паттерн ЕСТЬ в выводе → risk_feedback confirmed=True
- run_next: risk-задача, паттерна НЕТ в выводе → risk_feedback confirmed=False,
  streak снижен в файле
- run_next: не-risk задача → без ключа risk_feedback
- run_next: timeout → без risk_feedback
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


def risk_data(pattern="gateway timeout", streak=4):
    """Данные с real-историей (для predict_risks нужен len(history) >= 3)."""
    now = time.time()
    return {
        "history": [
            {"timestamp": now - 3600, "patterns": {pattern: 2}, "error_count": 1},
            {"timestamp": now - 7200, "patterns": {pattern: 1}, "error_count": 1},
            {"timestamp": now - 10800, "patterns": {pattern: 1}, "error_count": 1},
        ],
        "streaks": {pattern: streak},
        "learned_patterns": [], "last_update": 0,
        "cooccurrences": {}, "companions": [],
        "module_cooccurrences": {}, "module_companions": [],
    }


def risk_file(pattern="gateway timeout", streak=4):
    """Минимальный файл с risks-списком (для очереди; predict_risks не нужен)."""
    return {
        "history": [], "streaks": {pattern: streak}, "learned_patterns": [],
        "last_update": 0, "cooccurrences": {}, "companions": [],
        "module_cooccurrences": {}, "module_companions": [],
        "risks": [{"pattern": pattern, "risk": "high", "trend": "stable"}],
    }


def run_cli(*args):
    env = {**os.environ, "AGI_PATTERNS_FILE": str(SHARED)}
    return subprocess.run(
        [sys.executable, LEARNER, *args], capture_output=True, text=True, env=env
    )


# --- Test 1: feedback_risk confirmed=False → streak 4→2, журнал, saved ---
clean()
write(l.PATTERNS_FILE, risk_data())
rep = l.feedback_risk("gateway timeout", False)
assert rep.get("error") is None, rep
assert rep.get("confirmed") is False and rep.get("delta") == -2, rep
assert rep.get("streak_before") == 4 and rep.get("streak_after") == 2, rep
assert rep.get("saved") is True, rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 2, out["streaks"]
assert out["feedback"][-1]["type"] == "risk" and out["feedback"][-1]["confirmed"] is False
print("TEST 1 PASS: feedback_risk(False) → streak 4→2, журнал, saved")

# --- Test 2: confirmed=False на streak 3 → 1 → риск выпадает из predict_risks ---
clean()
write(l.PATTERNS_FILE, risk_data(streak=3))
l.feedback_risk("gateway timeout", False)
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 1, out["streaks"]
risks = l.predict_risks(out)
assert not any(r.get("pattern") == "gateway timeout" for r in risks), risks
print("TEST 2 PASS: streak 3→1 → predict_risks больше не даёт риск")

# --- Test 3: confirmed=True → streak НЕ трогается, журнал confirmed=True ---
clean()
write(l.PATTERNS_FILE, risk_data())
rep = l.feedback_risk("gateway timeout", True)
assert rep.get("error") is None, rep
assert rep.get("delta") == 0 and rep.get("streak_after") == 4, rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 4, out["streaks"]
assert out["feedback"][-1]["confirmed"] is True
print("TEST 3 PASS: feedback_risk(True) → streak не тронут, журнал")

# --- Test 4: floor — streak 1 → 0, не отрицательный ---
clean()
write(l.PATTERNS_FILE, risk_data(streak=1))
l.feedback_risk("gateway timeout", False)
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 0, out["streaks"]
print("TEST 4 PASS: floor — streak 1→0 (не минус)")

# --- Test 5: кастомный penalty ---
clean()
write(l.PATTERNS_FILE, risk_data(streak=5))
rep = l.feedback_risk("gateway timeout", False, penalty=3)
assert rep.get("streak_after") == 2, rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 2, out["streaks"]
print("TEST 5 PASS: кастомный penalty=3 → streak 5→2")

# --- Test 6: неизвестный паттерн → error, без журнала ---
clean()
write(l.PATTERNS_FILE, risk_data())
rep = l.feedback_risk("never seen", False)
assert rep.get("error") == "pattern not found", rep
out = json.loads(l.PATTERNS_FILE.read_text())
assert "feedback" not in out, out
print("TEST 6 PASS: неизвестный паттерн → error, журнал пуст")

# --- Test 7: пустой паттерн → error ---
clean()
write(l.PATTERNS_FILE, risk_data())
rep = l.feedback_risk("", False)
assert rep.get("error") == "empty pattern", rep
print("TEST 7 PASS: пустой паттерн → error")

# --- Test 8: CLI feedback-risk <pattern> true → exit 0, streak цел ---
clean()
write(l.PATTERNS_FILE, risk_data())
proc = run_cli("feedback-risk", "gateway timeout", "true")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("confirmed") is True and rep.get("streak_after") == 4, rep
print("TEST 8 PASS: CLI feedback-risk true → exit 0, streak цел")

# --- Test 9: CLI feedback-risk <pattern> false → exit 0, streak снижен ---
clean()
write(l.PATTERNS_FILE, risk_data())
proc = run_cli("feedback-risk", "gateway timeout", "false")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("confirmed") is False and rep.get("streak_after") == 2, rep
print("TEST 9 PASS: CLI feedback-risk false → exit 0, streak 4→2")

# --- Test 10: CLI feedback-risk --penalty кастомный ---
clean()
write(l.PATTERNS_FILE, risk_data(streak=5))
proc = run_cli("feedback-risk", "gateway timeout", "false", "--penalty", "3")
assert proc.returncode == 0, f"exit={proc.returncode}: {proc.stderr}"
rep = json.loads(proc.stdout)
assert rep.get("streak_after") == 2, rep
print("TEST 10 PASS: CLI --penalty 3 → streak 5→2")

# --- Test 11: CLI невалидный confirmed → exit 1 ---
clean()
write(l.PATTERNS_FILE, risk_data())
proc = run_cli("feedback-risk", "gateway timeout", "maybe")
assert proc.returncode != 0, "ожидался exit 1"
print("TEST 11 PASS: CLI невалидный confirmed → exit 1")

# --- Test 12: CLI неизвестный паттерн → exit 1, error в отчёте ---
clean()
write(l.PATTERNS_FILE, risk_data())
proc = run_cli("feedback-risk", "never seen", "false")
assert proc.returncode != 0, "ожидался exit 1"
rep = json.loads(proc.stdout)
assert "error" in rep, rep
print("TEST 12 PASS: CLI неизвестный паттерн → exit 1 + error")

# --- Test 13: run_next risk-задача, паттерн ЕСТЬ в выводе → confirmed=True ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, risk_file())
q.TASK_ACTIONS = [  # risk-задача "Investigate and fix pattern: ..."
    (("pattern",), ["python3", str(ECHO)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert result.get("risk_confirmed") is True, result
assert result.get("risk_feedback", {}).get("confirmed") is True, result
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 4, out["streaks"]
print("TEST 13 PASS: run_next risk-фидбек confirmed=True (паттерн в выводе)")

# --- Test 14: run_next risk-задача, паттерна НЕТ → confirmed=False, streak ↓ ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, risk_file())
q.TASK_ACTIONS = [
    (("pattern",), ["python3", str(SILENT)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert result.get("risk_confirmed") is False, result
assert result.get("risk_feedback", {}).get("confirmed") is False, result
out = json.loads(l.PATTERNS_FILE.read_text())
assert out["streaks"]["gateway timeout"] == 2, out["streaks"]
print("TEST 14 PASS: run_next risk-фидбек confirmed=False → streak 4→2")

# --- Test 15: run_next не-risk задача → без ключа risk_feedback ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, {"history": [], "streaks": {}, "learned_patterns": [],
                        "last_update": 0, "cooccurrences": {}, "companions": [],
                        "module_cooccurrences": {}, "module_companions": []})
write(q.KNOWLEDGE_FILE, {"last_search": time.time() - 10 * 3600, "findings": []})
q.TASK_ACTIONS = [
    (("research cycle",), ["python3", str(SILENT)]),
]
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert "risk_feedback" not in result, result
assert "risk_confirmed" not in result, result
print("TEST 15 PASS: run_next не-risk → без risk_feedback")

# --- Test 16: run_next timeout → без risk_feedback (нет вывода) ---
clean()
warm_defaults()
write(l.PATTERNS_FILE, risk_file())
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
assert "risk_feedback" not in result, result
print("TEST 16 PASS: run_next timeout → без risk_feedback")

print("\nALL TESTS PASSED")
