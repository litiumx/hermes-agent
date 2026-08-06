#!/usr/bin/env python3
"""Юнит-тест directed re-research + dedup для agi_self_directed_queue (05-06.08.2026)."""
import json
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


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


# --- Test 1: stale findings → directed задачи с приоритетом от возраста ---
clean()
now = time.time()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "old topic A", "timestamp": now - 100 * 3600, "sources": [{"url": "a"}]},
        {"topic": "fresh topic B", "timestamp": now - 2 * 3600, "sources": [{"url": "b"}]},
        {"topic": "old topic C", "timestamp": now - 50 * 3600, "sources": [{"url": "c"}]},
        {"topic": "old topic D", "timestamp": now - 200 * 3600, "sources": [{"url": "d"}]},
    ],
    "topics_searched": ["old topic A", "fresh topic B", "old topic C", "old topic D"],
    "last_search": now - 1,
})
queue = q.build_queue()
stale_tasks = [t for t in queue if t["source"] == "stale_topic"]
assert len(stale_tasks) == 3, f"ожидал 3 directed задачи (лимит 3), было {len(stale_tasks)}"
topics = {t["task"] for t in stale_tasks}
assert "Run curious agent research cycle for topic: old topic D" in topics, topics
assert "Run curious agent research cycle for topic: old topic A" in topics, topics
# fresh topic B не должна попасть
assert not any("fresh topic B" in t["task"] for t in stale_tasks), stale_tasks
# приоритет растёт с возрастом: D (200ч) > A (100ч) > C (50ч)
prio = {t["task"].split("topic: ")[1]: t["priority"] for t in stale_tasks}
assert prio["old topic D"] > prio["old topic A"] > prio["old topic C"], prio
assert prio["old topic D"] <= 55, prio  # кап
# generic-задача НЕ должна дублироваться при наличии stale
assert sum(1 for t in queue if t["task"] == "Run curious agent research cycle") == 0, queue
print("TEST 1 PASS: directed stale-topic задачи, приоритет по возрасту, кап, без generic-дубля")

# --- Test 2: fresh findings → stale нет, generic fallback из knowledge_gaps ---
clean()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "fresh", "timestamp": now - 1 * 3600, "sources": []}],
    "last_search": now - 100 * 3600,  # давно не исследовали → gap
})
queue = q.build_queue()
assert not any(t["source"] == "stale_topic" for t in queue), queue
gen = [t for t in queue if t["source"] == "knowledge_gap"]
assert len(gen) == 1 and gen[0]["task"] == "Run curious agent research cycle", queue
print("TEST 2 PASS: свежие находки → без directed, generic fallback работает")

# --- Test 3: dedup по тексту задачи ---
clean()
write(q.BRIDGE_FILE, {"pending_tasks": ["Fix the bug in bridge", "Fix the bug in bridge",
                                        "Fix the bug in bridge"], "last_task": "", "last_error": ""})
queue = q.build_queue()
same = [t for t in queue if t["task"] == "Fix the bug in bridge"]
assert len(same) == 1, f"дубликаты не схлопнулись: {len(same)}"
# приоритет не изменился после дедупа
assert same[0]["priority"] == 100, same
print("TEST 3 PASS: дубли pending схлопнуты в одну задачу с max приоритетом")

# --- Test 4: совместимость с TASK_ACTIONS (mapping для runner) ---
cmd = q._match_action("Run curious agent research cycle for topic: old topic D")
assert cmd is not None and "agi_curious_agent.py" in cmd[-1], cmd
print("TEST 4 PASS: directed-задача мапится на agi_curious_agent.py")

# --- Test 5: регрессия — пустой state не ломает build_queue ---
clean()
queue = q.build_queue()
assert isinstance(queue, list)
assert q.get_next_task() is None or isinstance(q.get_next_task(), dict)
print("TEST 5 PASS: пустое состояние → чистая очередь, без исключений")

print("\nALL TESTS PASS")
