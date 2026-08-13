#!/usr/bin/env python3
"""Юнит-тест knowledge_gap → directed topic (цикл 20, grow point 19→20).

Цель: knowledge_gap-задачи (нет исследований > 6ч, stale-тем нет) несут
КОНКРЕТНУЮ тему — самую старую находку из curious_knowledge (кандидат на
re-research). Пустая база / находки без timestamp → generic-задача без темы
(не выдумываем). run_next пробрасывает тему в curious_agent topic-режим.
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
    """Прогреть кулдаун дефолтных задач (иначе health check prio 40/self-improve
    prio 35 конкурируют с research prio 50 и засоряют очередь)."""
    now = time.time()
    write(q.QUEUE_FILE, {"history": [
        {"task": "Run system health check and proactive scan", "ts": now - 60},
        {"task": "Run self-improvement cycle (self_improve.py)", "ts": now - 60},
    ]})


def gap_tasks(queue):
    return [t for t in queue if t.get("source") == "knowledge_gap"]


# --- Test 1: gap-задача несёт САМУЮ СТАРУЮ находку как directed-тему ---
clean()
warm_defaults()
now = time.time()
# Обе находки свежее 24ч (иначе stale_topics заблокирует gap), last_search давно
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "fresh B", "timestamp": now - 2 * 3600, "sources": [{"url": "b"}]},
        {"topic": "older A", "timestamp": now - 20 * 3600, "sources": [{"url": "a"}]},
    ],
    "topics_searched": ["fresh B", "older A"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1, f"ожидался 1 gap-таск, got {len(gaps)}"
assert gaps[0]["task"] == "Run curious agent research cycle for topic: older A", gaps[0]
print("TEST 1 PASS: knowledge_gap несёт самую старую находку (directed topic)")

# --- Test 2: пустая база знаний → generic-задача БЕЗ темы ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [],
    "topics_searched": [],
    "last_search": time.time() - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1, f"ожидался 1 gap-таск, got {len(gaps)}"
assert gaps[0]["task"] == "Run curious agent research cycle", gaps[0]
print("TEST 2 PASS: пустая база → generic-задача без 'for topic:'")

# --- Test 3: находки без timestamp → generic (консервативно, тему не гадаем) ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "no-ts topic", "sources": [{"url": "x"}]},
        {"topic": "also no-ts", "sources": [{"url": "y"}]},
    ],
    "topics_searched": ["no-ts topic"],
    "last_search": time.time() - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1 and gaps[0]["task"] == "Run curious agent research cycle", gaps
print("TEST 3 PASS: находки без timestamp → generic-задача")

# --- Test 4: приоритет gap-задачи сохранён (min(hours*5, 50)) ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "only", "timestamp": time.time() - 3 * 3600,
                  "sources": [{"url": "o"}]}],
    "topics_searched": ["only"],
    "last_search": time.time() - 12 * 3600,  # 12ч → prio 50
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert gaps and gaps[0]["priority"] == 50, gaps
print("TEST 4 PASS: приоритет gap = min(hours*5, 50)")

# --- Test 5: run_next пробрасывает gap-тему в cmd curious_agent ---
clean()
warm_defaults()
now = time.time()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "gap topic X", "timestamp": now - 13 * 3600,
                  "sources": [{"url": "g"}]}],
    "topics_searched": ["gap topic X"],
    "last_search": now - 100 * 3600,
})
captured = {}
orig_run = subprocess.run


def fake_run(cmd, **kw):
    captured["cmd"] = cmd

    class R:
        returncode = 0
        stdout = "ok"
        stderr = ""
    return R()


subprocess.run = fake_run
res = q.run_next()
subprocess.run = orig_run
assert res["status"] == "done", res
assert captured["cmd"][-2:] == ["topic", "gap topic X"], captured["cmd"]
print("TEST 5 PASS: run_next → curious_agent topic 'gap topic X'")

# --- Test 6: регрессия — при stale_topics gap-задача НЕ создаётся ---
clean()
warm_defaults()
now = time.time()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "very stale", "timestamp": now - 100 * 3600, "sources": [{"url": "v"}]},
        {"topic": "fresh", "timestamp": now - 2 * 3600, "sources": [{"url": "f"}]},
    ],
    "topics_searched": ["very stale", "fresh"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
assert not gap_tasks(queue), "gap-задача не должна создаваться при stale_topics"
stale_tasks = [t for t in queue if t.get("source") == "stale_topic"]
assert len(stale_tasks) == 1 and stale_tasks[0]["task"].endswith("very stale"), stale_tasks
print("TEST 6 PASS: stale_topics блокирует knowledge_gap (регрессия)")

print("\nALL TESTS PASS (6)")
