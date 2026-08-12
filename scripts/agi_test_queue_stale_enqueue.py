#!/usr/bin/env python3
"""Standalone-тесты связки prune → self_directed_queue (цикл 18, grow point 17).

Покрытие: enqueue_topic (базовый/dedup/кулдаун/границы), prune_stale_topics
возвращает re_research-кандидатов (stale + score >= min_score), сортировку,
enqueue_re_research end-to-end и CLI-путь prune → очередь. Без сети.
"""
import contextlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_self_directed_queue as q
import agi_curious_agent as ca

TMP = Path(tempfile.mkdtemp())
q.QUEUE_FILE = TMP / "task_queue.json"
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = TMP / "error_patterns.json"
q.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"
ca.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"

DAY = 86400
NOW = time.time()


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def finding(topic, age_days, n_sources, with_snippet=True):
    src = []
    for i in range(n_sources):
        s = {"title": f"t{i}", "url": f"https://e{i}.x"}
        if with_snippet:
            s["snippet"] = f"s{i}"
        src.append(s)
    return {"topic": topic, "timestamp": NOW - age_days * DAY, "sources": src}


def clean():
    for p in (q.QUEUE_FILE, q.KNOWLEDGE_FILE):
        if p.exists():
            p.unlink()


# --- 1. enqueue_topic: базовый ---
clean()
ok = q.enqueue_topic("deep dive: SQLite WAL", priority=44)
assert ok is True, "первый enqueue должен вернуть True"
data = json.loads(q.QUEUE_FILE.read_text())
tasks = data["queue"]
assert len(tasks) == 1, tasks
t = tasks[0]
assert t["task"] == "Run curious agent research cycle for topic: deep dive: SQLite WAL", t
assert t["category"] == "research" and t["priority"] == 44 and t["source"] == "stale_prune", t
print("TEST 1 PASS: enqueue_topic добавляет directed-задачу с корректными полями")

# --- 2. enqueue_topic: dedup (уже в очереди) ---
ok = q.enqueue_topic("deep dive: SQLite WAL", priority=44)
assert ok is False, "повторный enqueue той же темы → False (dedup)"
tasks = json.loads(q.QUEUE_FILE.read_text())["queue"]
assert len(tasks) == 1, tasks
print("TEST 2 PASS: dedup — тема не дублируется в очереди")

# --- 3. enqueue_topic: кулдаун (задача в history за DEFAULT_COOLDOWN) ---
clean()
write(q.QUEUE_FILE, {"history": [
    {"task": "Run curious agent research cycle for topic: hot topic",
     "ts": NOW - 60, "ts_human": "x", "status": "done"},
]})
ok = q.enqueue_topic("hot topic")
assert ok is False, "задача исполнялась недавно → кулдаун, False"
tasks = json.loads(q.QUEUE_FILE.read_text()).get("queue", [])
assert len(tasks) == 0, tasks
print("TEST 3 PASS: кулдаун — недавно исполненная задача не ре-квеится")

# --- 4. enqueue_topic: кулдаун истёк → можно снова ---
clean()
write(q.QUEUE_FILE, {"history": [
    {"task": "Run curious agent research cycle for topic: old topic",
     "ts": NOW - q.DEFAULT_COOLDOWN - 60, "ts_human": "x", "status": "done"},
]})
ok = q.enqueue_topic("old topic")
assert ok is True, "кулдаун истёк → True"
print("TEST 4 PASS: кулдаун истёк → тема снова enqueue-ится")

# --- 5. enqueue_topic: границы (пустая тема / не-str) ---
clean()
assert q.enqueue_topic("") is False
assert q.enqueue_topic(None) is False
assert q.enqueue_topic("   ") is False
assert not q.QUEUE_FILE.exists() or not json.loads(q.QUEUE_FILE.read_text()).get("queue")
print("TEST 5 PASS: пустая/не-str тема → False без записи")

# --- 6. prune_stale_topics возвращает re_research: только stale + score >= min_score ---
clean()
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("stale_low", 70, 0),      # stale, score 0 < 1 → removed
    finding("stale_high", 70, 4),     # stale, score 4 >= 1 → kept + re_research
    finding("fresh", 1, 3),           # свежая → kept, НЕ re_research
], "topics_searched": ["stale_low", "stale_high", "fresh"], "last_search": 0})
res = ca.prune_stale_topics(max_age_days=30, min_score=1.0)
assert res["removed"] == 1 and res["kept"] == 2, res
rr = res["re_research"]
assert [r["topic"] for r in rr] == ["stale_high"], rr
assert rr[0]["age_days"] == 70 and rr[0]["score"] == 4.5, rr
print("TEST 6 PASS: re_research = stale+ценные, low-score удалён, свежие не кандидаты")

# --- 7. re_research сортировка: самый старый первым ---
clean()
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("older", 90, 3),
    finding("newer", 45, 5),
], "topics_searched": [], "last_search": 0})
res = ca.prune_stale_topics(max_age_days=30, min_score=1.0)
assert [r["topic"] for r in res["re_research"]] == ["older", "newer"], res["re_research"]
print("TEST 7 PASS: re_research отсортирован по возрасту (старые первыми)")

# --- 8. enqueue_re_research end-to-end: prune-кандидаты попадают в очередь ---
clean()
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("stale_good", 80, 5),
    finding("stale_ok", 40, 2),
    finding("junk", 80, 0),
], "topics_searched": [], "last_search": 0})
res = ca.prune_stale_topics(max_age_days=30, min_score=1.0)
eq = ca.enqueue_re_research(res["re_research"], max_topics=3)
assert eq["candidates"] == 2 and eq["enqueued"] == 2, eq
tasks = json.loads(q.QUEUE_FILE.read_text())["queue"]
topics = [t["task"] for t in tasks]
assert any("stale_good" in x for x in topics) and any("stale_ok" in x for x in topics), topics
# Приоритет растёт с возрастом: 80 дн → cap, 40 дн → меньше
prios = {t["task"]: t["priority"] for t in tasks}
assert prios[list(prios)[0]] >= 30, prios
print("TEST 8 PASS: prune-кандидаты автоматически enqueue-ятся в очередь")

# --- 9. enqueue_re_research: пустой список → 0 без ошибок ---
clean()
eq = ca.enqueue_re_research([], max_topics=3)
assert eq["enqueued"] == 0 and eq["candidates"] == 0, eq
print("TEST 9 PASS: пустой re_research → enqueued 0")

# --- 10. CLI prune → очередь (интеграция через main) ---
clean()
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("cli_topic", 100, 3),
], "topics_searched": ["cli_topic"], "last_search": 0})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ca.main(["prune", "30", "1.0"])
out = json.loads(buf.getvalue())
assert out["removed"] == 0, out
assert out["re_research_enqueued"]["enqueued"] == 1, out
tasks = json.loads(q.QUEUE_FILE.read_text())["queue"]
assert any("cli_topic" in t["task"] for t in tasks), tasks
print("TEST 10 PASS: CLI 'prune' автоматически ставит ценные stale-темы в очередь")

# --- 11. CLI prune с отключённым enqueue (argv[3] == '0') ---
clean()
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("no_enq", 100, 3),
], "topics_searched": ["no_enq"], "last_search": 0})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ca.main(["prune", "30", "1.0", "0"])
out = json.loads(buf.getvalue())
assert out["re_research_enqueued"]["enqueued"] == 0, out
print("TEST 11 PASS: 'prune ... 0' отключает авто-enqueue")

# --- 12. Кулдаун в связке: тема из history не ре-квеится через CLI ---
clean()
write(q.QUEUE_FILE, {"history": [
    {"task": "Run curious agent research cycle for topic: cli_topic",
     "ts": NOW - 60, "ts_human": "x", "status": "done"},
]})
write(ca.KNOWLEDGE_FILE, {"findings": [
    finding("cli_topic", 100, 3),
], "topics_searched": ["cli_topic"], "last_search": 0})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ca.main(["prune", "30", "1.0"])
out = json.loads(buf.getvalue())
assert out["re_research_enqueued"]["enqueued"] == 0, out
tasks = json.loads(q.QUEUE_FILE.read_text()).get("queue", [])
assert not any("cli_topic" in t["task"] for t in tasks), tasks
print("TEST 12 PASS: кулдаун истории уважается в CLI-связке prune→queue")

print("\nALL 12 TESTS PASS (agi_test_queue_stale_enqueue.py)")
