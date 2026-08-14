#!/usr/bin/env python3
"""Юнит-тест петли обратной связи companion-предсказаний (цикл 26).

Grow point (цикл 25): планировщик создаёт пре-емптивные fix-задачи из
companions learner'а, но после исполнения НИКТО не сообщает, подтвердился
ли предсказанный паттерн — веса пар не корректируются, прогноз не учится
на своих ошибках. Этот тест проверяет:
- predict_companions / predict_module_companions включают anchors (какие
  активные паттерны породили предсказание — без них фидбек невозможен)
- feedback_companion(confirmed=True) усиливает пары (anchor, pattern)
- feedback_companion(confirmed=False) ослабляет пары (floor 0, удаление
  ниже COOCCUR_MIN_PAIRS — опровергнутый companion выпадает из прогноза)
- legacy-запись без anchors: fallback на правку co_score, без краха
- module-фидбек правит module_cooccurrences, global не трогает
- неизвестный паттерн — no-op с error, без журнала
- журнал feedback: кап FEEDBACK_JOURNAL_MAX
- build_queue: companion-задачи несут pattern/module для фидбека
- mark_completed: companion-задача → фидбек в learner (усиление/ослабление)
- mark_completed: не-companion задача / отсутствующий patterns.json — тишина
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_error_pattern_learner as l
import agi_self_directed_queue as q

TMP = Path(tempfile.mkdtemp())
SHARED = TMP / "error_patterns.json"
l.PATTERNS_FILE = SHARED
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = SHARED  # тот же файл — mark_completed пишет туда, откуда build_queue читает
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


def base_data(co=None, companions=None, module_co=None, module_companions=None):
    return {
        "history": [], "streaks": {}, "learned_patterns": [], "last_update": 0,
        "cooccurrences": co if co is not None else {"A": {"B": 2.0}, "B": {"A": 2.0}},
        "companions": companions if companions is not None
        else [{"pattern": "B", "co_score": 2.0, "anchors": ["A"]}],
        "module_cooccurrences": module_co or {},
        "module_companions": module_companions or [],
    }


# --- Test 1: predict_companions включает anchors (какие активные паттерны дали прогноз) ---
clean()
data = {
    "history": [
        {"timestamp": time.time(), "error_count": 5, "patterns": {"A": 2, "B": 3}, "sources": {}},
        {"timestamp": time.time(), "error_count": 2, "patterns": {"A": 1, "B": 1}, "sources": {}},
    ]
}
comps = l.predict_companions(data, {"A": 1})
assert comps, "ожидался companion-кандидат"
assert comps[0]["pattern"] == "B", comps[0]
assert comps[0]["anchors"] == ["A"], f"anchors должны быть [A], got {comps[0].get('anchors')}"
print("TEST 1 PASS: predict_companions включает anchors")

# --- Test 2: feedback confirmed=True усиливает пары (обе стороны) ---
clean()
write(l.PATTERNS_FILE, base_data())
rep = l.feedback_companion("B", True)
out = json.loads(l.PATTERNS_FILE.read_text())
assert rep.get("adjusted_pairs") == 1, rep
assert out["cooccurrences"]["A"]["B"] == 3.0, out["cooccurrences"]
assert out["cooccurrences"]["B"]["A"] == 3.0, out["cooccurrences"]
assert out["feedback"][-1]["confirmed"] is True
print("TEST 2 PASS: confirmed=True усиливает пары (3.0)")

# --- Test 3: feedback confirmed=False ослабляет пары ---
clean()
write(l.PATTERNS_FILE, base_data(co={"A": {"B": 2.5}, "B": {"A": 2.5}}))
rep = l.feedback_companion("B", False)
out = json.loads(l.PATTERNS_FILE.read_text())
assert rep.get("adjusted_pairs") == 1, rep
assert out["cooccurrences"]["A"]["B"] == 2.0, out["cooccurrences"]
assert out["feedback"][-1]["confirmed"] is False
print("TEST 3 PASS: confirmed=False ослабляет пары (2.0)")

# --- Test 4: penalty floor — пара ниже COOCCUR_MIN_PAIRS удаляется ---
clean()
write(l.PATTERNS_FILE, base_data(co={"A": {"B": 0.3}, "B": {"A": 0.3}}))
l.feedback_companion("B", False)
out = json.loads(l.PATTERNS_FILE.read_text())
assert "A" not in out["cooccurrences"] or "B" not in out["cooccurrences"].get("A", {}), \
    f"пара ниже min_pairs должна быть удалена: {out['cooccurrences']}"
assert "B" not in out["cooccurrences"] or "A" not in out["cooccurrences"].get("B", {}), \
    f"симметричная пара должна быть удалена: {out['cooccurrences']}"
print("TEST 4 PASS: floor 0 + удаление пар ниже COOCCUR_MIN_PAIRS")

# --- Test 5: legacy запись без anchors — fallback на co_score, без краха ---
clean()
write(l.PATTERNS_FILE, base_data(companions=[{"pattern": "B", "co_score": 4.0}]))
rep = l.feedback_companion("B", True)
out = json.loads(l.PATTERNS_FILE.read_text())
assert rep.get("adjusted_pairs") == 0, rep
assert out["companions"][0]["co_score"] == 5.0, out["companions"]
assert out.get("feedback"), "журнал должен пополниться даже при legacy-fallback"
print("TEST 5 PASS: legacy без anchors — правка co_score")

# --- Test 6: module-фидбек правит module_cooccurrences, global не трогает ---
clean()
write(l.PATTERNS_FILE, base_data(
    co={"A": {"B": 2.0}, "B": {"A": 2.0}},
    module_co={"gateway.log": {"A": {"B": 2.0}, "B": {"A": 2.0}}},
    module_companions=[{"pattern": "B", "source": "gateway.log", "co_score": 2.0,
                        "anchors": ["A"]}],
))
rep = l.feedback_companion("B", True, module="gateway.log")
out = json.loads(l.PATTERNS_FILE.read_text())
assert rep.get("adjusted_pairs") == 1, rep
assert out["module_cooccurrences"]["gateway.log"]["A"]["B"] == 3.0, out["module_cooccurrences"]
assert out["cooccurrences"]["A"]["B"] == 2.0, "global cooccurrences не должны меняться"
assert out["feedback"][-1]["module"] == "gateway.log"
print("TEST 6 PASS: module-фидбек правит только module_cooccurrences")

# --- Test 7: неизвестный паттерн — no-op с error, без журнала ---
clean()
write(l.PATTERNS_FILE, base_data())
rep = l.feedback_companion("UNKNOWN", True)
out = json.loads(l.PATTERNS_FILE.read_text())
assert rep.get("adjusted_pairs") == 0, rep
assert "error" in rep, rep
assert not out.get("feedback"), "неизвестный паттерн не должен писать журнал"
assert out["cooccurrences"]["A"]["B"] == 2.0, "пары не должны меняться"
print("TEST 7 PASS: неизвестный паттерн — no-op")

# --- Test 8: predict_module_companions включает anchors ---
clean()
data = {
    "history": [
        {"timestamp": time.time(), "error_count": 2, "patterns": {},
         "sources": {"gateway.log": {"A": 1, "B": 1}}},
        {"timestamp": time.time(), "error_count": 2, "patterns": {},
         "sources": {"gateway.log": {"A": 2, "B": 1}}},
    ]
}
comps = l.predict_module_companions(data, {"gateway.log": {"A": 1}})
assert comps, "ожидался module-companion"
assert comps[0]["pattern"] == "B", comps[0]
assert comps[0]["source"] == "gateway.log", comps[0]
assert comps[0]["anchors"] == ["A"], comps[0]
print("TEST 8 PASS: predict_module_companions включает anchors")

# --- Test 9: журнал feedback ограничен FEEDBACK_JOURNAL_MAX ---
clean()
for i in range(60):
    write(l.PATTERNS_FILE, base_data())
    l.feedback_companion("B", True)
out = json.loads(l.PATTERNS_FILE.read_text())
assert len(out["feedback"]) <= l.FEEDBACK_JOURNAL_MAX, \
    f"журнал должен быть capped: {len(out['feedback'])}"
print(f"TEST 9 PASS: журнал feedback capped ({l.FEEDBACK_JOURNAL_MAX})")

# --- Test 10: build_queue — companion-задачи несут pattern/module для фидбека ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, {
    "companions": [{"pattern": "global X", "co_score": 3.0}],
    "module_companions": [{"pattern": "mod Y", "source": "errors.log", "co_score": 4.0}],
})
queue = q.build_queue()
comps = [t for t in queue if t.get("source") == "companion"]
assert any(t.get("pattern") == "global X" and t.get("module") is None for t in comps), comps
assert any(t.get("pattern") == "mod Y" and t.get("module") == "errors.log" for t in comps), comps
print("TEST 10 PASS: companion-задачи несут pattern/module")

# --- Test 11: mark_completed(confirmed=True) усиливает пары (интеграция) ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, base_data())
queue = q.build_queue()
comps = [t for t in queue if t.get("source") == "companion"]
assert comps, queue
q.save_queue(queue)  # mark_completed читает очередь с диска
task = comps[0]["task"]
rep = q.mark_completed(task, confirmed=True)
out = json.loads(q.PATTERNS_FILE.read_text())
assert rep is not None and rep.get("adjusted_pairs") == 1, rep
assert out["cooccurrences"]["A"]["B"] == 3.0, out["cooccurrences"]
assert task not in [t["task"] for t in q.load_queue()], "задача должна быть удалена"
print("TEST 11 PASS: mark_completed(True) → усиление пар")

# --- Test 12: mark_completed(confirmed=False) ослабляет пары ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, base_data(co={"A": {"B": 2.5}, "B": {"A": 2.5}}))
queue = q.build_queue()
comps = [t for t in queue if t.get("source") == "companion"]
assert comps, queue
q.save_queue(queue)
rep = q.mark_completed(comps[0]["task"], confirmed=False)
out = json.loads(q.PATTERNS_FILE.read_text())
assert rep is not None and rep.get("adjusted_pairs") == 1, rep
assert out["cooccurrences"]["A"]["B"] == 2.0, out["cooccurrences"]
print("TEST 12 PASS: mark_completed(False) → ослабление пар")

# --- Test 13: mark_completed не-companion задачи — patterns.json не тронут ---
clean()
warm_defaults()
write(q.PATTERNS_FILE, {**base_data(), "risks": [
    {"pattern": "riskP", "risk": "high", "trend": "rising"},
]})
queue = q.build_queue()
risk_tasks = [t for t in queue if t.get("source") == "risk"]
assert risk_tasks, queue
q.save_queue(queue)
before = q.PATTERNS_FILE.read_text()
rep = q.mark_completed(risk_tasks[0]["task"], confirmed=True)
assert rep is None, rep
assert q.PATTERNS_FILE.read_text() == before, "не-companion задача не должна трогать patterns.json"
print("TEST 13 PASS: mark_completed не-companion — тишина")

# --- Test 14: mark_completed при отсутствующем patterns.json — без краха ---
clean()
write(q.QUEUE_FILE, {"queue": [
    {"task": "Investigate and fix pattern: Z (companion of active errors)",
     "category": "fix", "priority": 60, "source": "companion",
     "pattern": "Z", "module": None},
]})
rep = q.mark_completed("Investigate and fix pattern: Z (companion of active errors)",
                       confirmed=True)
assert rep is None or "error" in rep, rep  # файла нет → тихий отказ, не крах
print("TEST 14 PASS: mark_completed без patterns.json — без краха")

print("\nALL TESTS PASSED")
