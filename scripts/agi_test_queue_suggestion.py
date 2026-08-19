#!/usr/bin/env python3
"""Юнит-тест: self_directed_queue потребляет suggestion из predict_risks (19.08.2026, цикл 44).

Grow point цикла 43 (SELF_IMPROVE_2026-08-18): predict_risks пишет
suggestion в каждый риск, но планировщик его выкидывал — рекомендация
(напр. TWO-STRIKE RULE для tool_call_loop) существовала только в отчёте
learner'а. Теперь: suggestion проходит load_state → задача очереди →
run_next/отчёт.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_self_directed_queue as q

# Hermetic: отключаем SQLite-bridge (в песочнице живёт реальный bridge с
# остатками прошлых тестов) — JSON-fallback на TMP-файлы детерминирован.
q._USE_BRIDGE = False

TMP = Path(tempfile.mkdtemp())
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = TMP / "error_patterns.json"
q.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"
q.QUEUE_FILE = TMP / "task_queue.json"


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


# --- Test 1: suggestion из predict_risks доходит до задачи очереди ---
clean()
write(q.PATTERNS_FILE, {
    "risks": [{
        "risk": "high",
        "pattern": "tool_call_loop:web_search",
        "trend": "stable",
        "decay_score": 0.9,
        "suggestion": ("Зацикливание тула: смени подход, TWO-STRIKE RULE — "
                       "после 2 неудач СТОП, при деградации контекста /compact."),
    }],
    "streaks": {"tool_call_loop:web_search": 3},
})
queue = q.build_queue()
risk_tasks = [t for t in queue if t["source"] == "risk"]
assert len(risk_tasks) == 1, risk_tasks
t = risk_tasks[0]
assert t.get("suggestion"), f"suggestion не перенесён в задачу: {t}"
assert "TWO-STRIKE" in t["suggestion"], t["suggestion"]
print("TEST 1 PASS: suggestion из риска перенесён в задачу очереди")

# --- Test 2: suggestion доходит через load_state ---
clean()
write(q.PATTERNS_FILE, {
    "risks": [{
        "risk": "high",
        "pattern": "pattern-a",
        "trend": "up",
        "suggestion": "Проверить сервис X.",
    }],
    "streaks": {"pattern-a": 5},
})
state = q.load_state()
assert len(state["risks"]) == 1, state["risks"]
assert state["risks"][0]["suggestion"] == "Проверить сервис X.", state["risks"]
print("TEST 2 PASS: load_state сохраняет suggestion из риска")

# --- Test 3: риск без suggestion → поле None, без падения ---
clean()
write(q.PATTERNS_FILE, {
    "risks": [{"risk": "high", "pattern": "pattern-b", "trend": "stable"}],
    "streaks": {"pattern-b": 4},
})
state = q.load_state()
assert state["risks"][0]["suggestion"] is None, state["risks"]
queue = q.build_queue()
risk_tasks = [t for t in queue if t["source"] == "risk"]
assert len(risk_tasks) == 1
assert risk_tasks[0].get("suggestion") is None, risk_tasks[0]
print("TEST 3 PASS: риск без suggestion → None, очередь строится")

# --- Test 4: fallback без risks (старый файл) → без suggestion, без падения ---
clean()
write(q.PATTERNS_FILE, {"streaks": {"old-pattern": 3}})
state = q.load_state()
assert len(state["risks"]) == 1, state["risks"]
assert "suggestion" not in state["risks"][0] or state["risks"][0].get("suggestion") is None
queue = q.build_queue()
assert isinstance(queue, list)
print("TEST 4 PASS: старый формат файла не ломается")

# --- Test 5: suggestion пробрасывается в run_next результат ---
clean()
# Моно-тест: перехватываем _match_action, чтобы не запускать реальный скрипт
orig_match = q._match_action
q._match_action = lambda task: None  # задача будет "skipped", но с suggestion
write(q.PATTERNS_FILE, {
    "risks": [{
        "risk": "high",
        "pattern": "loop-a",
        "trend": "stable",
        "suggestion": "Сменить подход, TWO-STRIKE RULE.",
    }],
    "streaks": {"loop-a": 3},
})
result = q.run_next()
q._match_action = orig_match
assert result is not None and result.get("task"), result
assert result.get("suggestion") == "Сменить подход, TWO-STRIKE RULE.", result
print("TEST 5 PASS: run_next возвращает suggestion в результате")

# --- Test 6: suggestion в get_report (fix-задачи) ---
clean()
write(q.PATTERNS_FILE, {
    "risks": [{
        "risk": "high",
        "pattern": "report-pattern",
        "trend": "stable",
        "suggestion": "Проверить соответствующие сервисы.",
    }],
    "streaks": {"report-pattern": 3},
})
report = q.get_report()
assert "report-pattern" in report, report
print("TEST 6 PASS: get_report работает с рисками, несущими suggestion")

# --- Test 7: пустой/невалидный suggestion → None, без падения ---
clean()
for bad in (None, "", "   ", 123, ["x"]):
    write(q.PATTERNS_FILE, {
        "risks": [{"risk": "high", "pattern": "p-bad", "trend": "stable",
                   "suggestion": bad}],
        "streaks": {"p-bad": 3},
    })
    state = q.load_state()
    assert state["risks"][0]["suggestion"] is None, (bad, state["risks"])
print("TEST 7 PASS: пустой/невалидный suggestion нормализуется в None")

# --- Test 8: регрессия — все источники задач по-прежнему строятся ---
clean()
write(q.BRIDGE_FILE, {"pending_tasks": ["Fix the bug in bridge"],
                      "last_task": "", "last_error": ""})
write(q.PATTERNS_FILE, {
    "risks": [{"risk": "high", "pattern": "p-reg", "trend": "stable",
               "suggestion": "Совет по p-reg."}],
    "streaks": {"p-reg": 3},
    "companions": [{"pattern": "comp-p", "co_score": 70, "source": "svc.log"}],
})
queue = q.build_queue()
sources = {t["source"] for t in queue}
assert "pending" in sources and "risk" in sources and "companion" in sources, sources
assert all("suggestion" in t for t in queue if t["source"] == "risk")
print("TEST 8 PASS: все источники задач целы, risk несёт suggestion")

print("\nALL TESTS PASS")
