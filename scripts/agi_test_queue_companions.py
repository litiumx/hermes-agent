#!/usr/bin/env python3
"""Юнит-тест интеграции companion-предсказаний в планировщик (цикл 25).

Grow point (циклы 21-24): error_pattern_learner персистит companions /
module_companions в patterns.json, но self_directed_queue их НЕ читает —
пре-емптивные задачи для паттернов, предсказанных «прийти следующими»,
не создавались. Этот тест проверяет:
- load_state читает companions/module_companions из patterns.json
- build_queue создаёт fix-задачи source="companion" с приоритетом от co_score
- паттерн, уже покрытый риском, НЕ дублируется companion-задачей
- кулдаун: недавно исполненная companion-задача не пере-добавляется
- cap: максимум COMPANION_MAX_TASKS задач из companions
- malformed данные (не список/без pattern) не роняют очередь
- run_next исполняет companion-задачу (маппинг "pattern")
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_self_directed_queue as q

TMP = Path(tempfile.mkdtemp())
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = TMP / "error_patterns.json"
q.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"
q.QUEUE_FILE = TMP / "task_queue.json"

TMP_BIN = TMP / "bin"
TMP_BIN.mkdir(exist_ok=True)
for _s in ("proactive_scan.py", "self_improve.py", "agi_curious_agent.py",
           "agi_error_pattern_learner.py"):
    (TMP_BIN / _s).write_text("")
q.TASK_ACTIONS = [
    (("proactive scan", "health check"), ["python3", str(TMP_BIN / "proactive_scan.py")]),
    (("self_improve", "self-improvement"), ["python3", str(TMP_BIN / "self_improve.py")]),
    (("curious agent", "research cycle"), ["python3", str(TMP_BIN / "agi_curious_agent.py")]),
    (("pattern",), ["python3", str(TMP_BIN / "agi_error_pattern_learner.py"), "report"]),
]


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


def warm_defaults():
    """Прогреть кулдаун дефолтных задач (иначе они конкурируют за очередь)."""
    now = time.time()
    write(q.QUEUE_FILE, {"history": [
        {"task": "Run system health check and proactive scan", "ts": now - 60},
        {"task": "Run self-improvement cycle (self_improve.py)", "ts": now - 60},
    ]})


def companion_tasks(queue):
    return [t for t in queue if t.get("source") == "companion"]


def patterns_with(companions, module_companions=None, risks=None):
    p = {"companions": companions}
    if module_companions is not None:
        p["module_companions"] = module_companions
    if risks is not None:
        p["risks"] = risks
    return p


# --- Test 1: companions из patterns.json → fix-задачи source="companion" ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([
    {"pattern": "gateway timeout", "co_score": 4.0,
     "message": "Паттерн 'gateway timeout' исторически появляется вместе"},
]))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 1, f"ожидался 1 companion-таск, got {len(comps)}"
assert comps[0]["category"] == "fix", comps[0]
assert "gateway timeout" in comps[0]["task"], comps[0]
assert comps[0]["priority"] >= 50, comps[0]
print("TEST 1 PASS: companions → fix-задача source=companion")

# --- Test 2: приоритет масштабируется от co_score (выше score → выше приоритет) ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([
    {"pattern": "low score", "co_score": 1.0},
    {"pattern": "high score", "co_score": 6.0},
]))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 2, f"ожидалось 2, got {len(comps)}"
prio = {c["task"]: c["priority"] for c in comps}
assert prio["Investigate and fix pattern: high score (companion of active errors)"] == 90, prio
assert prio["Investigate and fix pattern: low score (companion of active errors)"] == 58, prio
# Порядок: high score раньше low score
idx = [c["task"] for c in comps]
assert idx.index("Investigate and fix pattern: high score (companion of active errors)") < \
       idx.index("Investigate and fix pattern: low score (companion of active errors)"), idx
print("TEST 2 PASS: приоритет от co_score (кап 90), сортировка по score")

# --- Test 3: паттерн, покрытый риском, НЕ дублируется companion-задачей ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with(
    [{"pattern": "db deadlock", "co_score": 5.0}],
    risks=[{"pattern": "db deadlock", "risk": "high", "trend": "rising"}],
))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 0, f"companion-дубль риска не должен создаваться: {comps}"
fixes = [t for t in queue if t["category"] == "fix"]
assert len(fixes) == 1, f"ровно 1 fix-задача (от риска), got {len(fixes)}"
print("TEST 3 PASS: паттерн из рисков не дублируется companion-задачей")

# --- Test 4: кулдаун — недавно исполненная companion-задача не пере-добавляется ---
clean()
warm_defaults()
now = time.time()
write(q.PATTERNS_FILE, patterns_with([{"pattern": "cache miss storm", "co_score": 3.0}]))
# Задача уже исполнялась 1 час назад
write(q.QUEUE_FILE, {"history": [
    {"task": "Run system health check and proactive scan", "ts": now - 60},
    {"task": "Run self-improvement cycle (self_improve.py)", "ts": now - 60},
    {"task": "Investigate and fix pattern: cache miss storm (companion of active errors)",
     "ts": now - 3600},
]})
queue = q.build_queue()
assert len(companion_tasks(queue)) == 0, "задача в кулдауне не должна пере-добавляться"
print("TEST 4 PASS: companion-задача уважает кулдаун DEFAULT_COOLDOWN")

# --- Test 5: пустые/отсутствующие companions → нет companion-задач, нет краха ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, {"risks": []})
queue = q.build_queue()
assert len(companion_tasks(queue)) == 0, "пустые companions не дают задач"
clean()
warm_defaults()
queue = q.build_queue()  # вообще без patterns.json
assert len(companion_tasks(queue)) == 0
print("TEST 5 PASS: пустые/отсутствующие companions — тишина")

# --- Test 6: malformed companions (не список / без pattern) не роняют очередь ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with("not-a-list"))
queue = q.build_queue()
assert len(companion_tasks(queue)) == 0
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([
    {"co_score": 3.0},          # без pattern
    {"pattern": ""},            # пустой pattern
    {"pattern": "ok pattern", "co_score": 2.0},
]))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 1, f"валидный companion должен выжить: {comps}"
assert "ok pattern" in comps[0]["task"]
print("TEST 6 PASS: malformed companions отфильтрованы, валидный выжил")

# --- Test 7: module_companions → задача с модулем в тексте ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with(
    [], module_companions=[{"pattern": "gateway 502", "source": "gateway.log",
                            "co_score": 3.5}],
))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 1, f"ожидался module companion, got {len(comps)}"
assert "gateway 502" in comps[0]["task"] and "gateway.log" in comps[0]["task"], comps[0]
assert comps[0]["category"] == "fix"
print("TEST 7 PASS: module_companions → задача с указанием модуля")

# --- Test 8: один и тот же паттерн в companions и module_companions → 1 задача ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with(
    [{"pattern": "shared pattern", "co_score": 2.0}],
    module_companions=[{"pattern": "shared pattern", "source": "errors.log",
                        "co_score": 4.0}],
))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) == 1, f"дедуп по pattern: ожидалась 1 задача, got {len(comps)}"
# Лучший (макс co_score) вариант: module 4.0 > global 2.0
assert "errors.log" in comps[0]["task"], comps[0]
print("TEST 8 PASS: дедуп companions/module_companions по pattern, лучший co_score")

# --- Test 9: cap — максимум COMPANION_MAX_TASKS задач из companions ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([
    {"pattern": f"pattern {i}", "co_score": float(10 - i)} for i in range(1, 6)
]))
queue = q.build_queue()
comps = companion_tasks(queue)
assert len(comps) <= q.COMPANION_MAX_TASKS, \
    f"cap {q.COMPANION_MAX_TASKS} нарушен: {len(comps)}"
# Самые сильные companions должны быть в очереди
assert any("pattern 1" in c["task"] for c in comps)
print("TEST 9 PASS: cap companions до COMPANION_MAX_TASKS")

# --- Test 10: run_next исполняет companion-задачу (маппинг "pattern") ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([{"pattern": "top companion", "co_score": 9.0}]))
result = q.run_next()
assert result is not None and result.get("status") == "done", result
assert "top companion" in result.get("task", ""), result
print("TEST 10 PASS: run_next исполнил companion-задачу")

# --- Test 11: load_state отдаёт companions в state ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, patterns_with([{"pattern": "state pattern", "co_score": 2.5}]))
state = q.load_state()
assert len(state.get("companions", [])) == 1, state.get("companions")
assert state["companions"][0]["pattern"] == "state pattern"
print("TEST 11 PASS: load_state отдаёт companions")

# --- Test 12: legacy файл (без companions/risks) не роняет load_state ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, {"streaks": {"old pattern": 7}})
state = q.load_state()
queue = q.build_queue()
assert state.get("companions") == []
assert len(companion_tasks(queue)) == 0
print("TEST 12 PASS: legacy patterns.json — тишина, без краха")

print("\nALL TESTS PASSED")
