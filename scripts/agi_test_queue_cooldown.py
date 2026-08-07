#!/usr/bin/env python3
"""Юнит-тест cooldown для pending/stale-topics в agi_self_directed_queue (цикл 4, 07.08.2026).

Баг: pending-задачи из bridge и directed stale-темы не имели кулдауна —
каждый build_queue() возвращал их заново (циклы каждые 30-60 мин),
плодя повторы в history и спам-прогоны одной задачи.
"""
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


def seed_history(tasks_with_ts):
    """Записать history в QUEUE_FILE: [(task, ts), ...]."""
    write(q.QUEUE_FILE, {
        "updated": time.time(),
        "queue": [],
        "history": [{"task": t, "ts": ts, "status": s} for t, ts, s in tasks_with_ts],
    })


NOW = time.time()
CD = q.DEFAULT_COOLDOWN

# --- Test 1: pending-задача из bridge, исполненная недавно → НЕ ре-квеится ---
clean()
seed_history([("check disk space", NOW - 3600, "done")])
write(q.BRIDGE_FILE, {"pending_tasks": ["check disk space", "fresh task X"]})
queue = q.build_queue()
assert not any(t["task"] == "check disk space" for t in queue), \
    f"задача с кулдауном не должна вернуться: {[t['task'] for t in queue]}"
assert any(t["task"] == "fresh task X" for t in queue), \
    "свежая pending-задача должна попасть в очередь"
print("TEST 1 PASS: pending с кулдауном <6ч не ре-квеится, новая — попадает")

# --- Test 2: pending-задача, исполненная ДАВНО (>6ч) → снова доступна ---
clean()
seed_history([("check disk space", NOW - CD - 100, "done")])
write(q.BRIDGE_FILE, {"pending_tasks": ["check disk space"]})
queue = q.build_queue()
assert any(t["task"] == "check disk space" and t["source"] == "pending" for t in queue), queue
print("TEST 2 PASS: pending после истечения кулдауна возвращается")

# --- Test 3: stale-тема с недавним research в history → НЕ ре-квеится ---
clean()
topic_task = "Run curious agent research cycle for topic: old topic A"
seed_history([(topic_task, NOW - 1800, "failed")])
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "old topic A", "timestamp": NOW - 100 * 3600, "sources": []}],
    "last_search": NOW - 1,
})
queue = q.build_queue()
assert not any(t["task"] == topic_task for t in queue), \
    f"stale-тема с недавним прогоном не должна ре-квеиться: {[t['task'] for t in queue]}"
# НО generic fallback не должен появиться (stale есть, просто в кулдауне)
assert not any(t["source"] == "knowledge_gap" for t in queue), queue
print("TEST 3 PASS: stale-тема с недавним фейлом research не ре-квеится (и generic не дублирует)")

# --- Test 4: stale-тема без недавнего прогона → directed задача есть ---
clean()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "old topic A", "timestamp": NOW - 100 * 3600, "sources": []}],
    "last_search": NOW - 1,
})
queue = q.build_queue()
assert any(t["task"] == topic_task for t in queue), queue
print("TEST 4 PASS: stale-тема без кулдауна → directed задача в очереди")

# --- Test 5: run_next интеграция — skipped-задача попадает в history и
#     следующий build_queue() её не возвращает (полный цикл без дублей) ---
clean()
write(q.BRIDGE_FILE, {"pending_tasks": ["no mapping task Z"]})
r = q.run_next()
assert r and r["status"] == "skipped", f"ожидал skipped, было {r}"
queue = q.build_queue()
assert not any(t["task"] == "no mapping task Z" for t in queue), \
    f"после run_next задача не должна вернуться в очереди: {[t['task'] for t in queue]}"
print("TEST 5 PASS: run_next → skipped записан в history → задача в кулдауне")

# --- Test 6: регрессия risks-кулдаун всё ещё работает ---
clean()
risk_task = "Investigate and fix pattern: some_error (trend: rising)"
seed_history([(risk_task, NOW - 3600, "failed")])
write(q.PATTERNS_FILE, {
    "risks": [{"pattern": "some_error", "risk": "high", "trend": "rising"}],
    "streaks": {"some_error": 5},
})
queue = q.build_queue()
assert not any(t["task"] == risk_task for t in queue), queue
print("TEST 6 PASS: регрессия — risk-задача с кулдауном не ре-квеится")

# --- Test 7: дубль pending в bridge (два одинаковых) → один в очереди ---
clean()
write(q.BRIDGE_FILE, {"pending_tasks": ["dup task", "dup task"]})
queue = q.build_queue()
dups = [t for t in queue if t["task"] == "dup task"]
assert len(dups) == 1, f"дедуп pending по тексту: {dups}"
print("TEST 7 PASS: дубли pending внутри одного bridge схлопываются в одну задачу")

print("\nALL TESTS PASS (7)")
