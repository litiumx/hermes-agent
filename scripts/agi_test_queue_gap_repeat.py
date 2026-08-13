#!/usr/bin/env python3
"""Юнит-тест repeat-фильтра knowledge_gap topic (цикл 23, grow point 22).

Цель: тема, исследованная < RESEARCH_REPEAT_HOURS (12ч) назад, НЕ выбирается
в knowledge_gap topic — даже если у неё есть старая находка (directed
re-research ЗАМЕНЯЕТ находку, но при дублях старая остаётся и раньше
побеждала как «самая старая»). Среди кандидатов — тема с самой старой
находкой (как в цикле 20). Все темы свежие → generic-задача без темы
(не долбим свежее — ре-исследование ничего не даст).
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


# --- Test 1: тема со СВЕЖЕЙ находкой (3ч) исключается, даже если есть старая (20ч) ---
clean()
warm_defaults()
now = time.time()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "old-but-recent A", "timestamp": now - 20 * 3600, "sources": [{"url": "a1"}]},
        # re-research 3ч назад: находка заменена, но при дублях старая осталась
        {"topic": "old-but-recent A", "timestamp": now - 3 * 3600, "sources": [{"url": "a2"}]},
        {"topic": "candidate C", "timestamp": now - 15 * 3600, "sources": [{"url": "c"}]},
    ],
    "topics_searched": ["old-but-recent A", "candidate C"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1, f"ожидался 1 gap-таск, got {len(gaps)}"
# Старый код взял бы 'old-but-recent A' (самая старая находка 20ч) — теперь она
# исключена (новейшая находка 3ч < 12ч), выбирается 'candidate C' (15ч).
assert gaps[0]["task"] == "Run curious agent research cycle for topic: candidate C", gaps[0]
print("TEST 1 PASS: тема со свежей находкой исключена, выбрана следующая по возрасту")

# --- Test 2: единственная тема исследована 5ч назад → generic БЕЗ темы ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "recent only", "timestamp": now - 5 * 3600, "sources": [{"url": "r"}]}],
    "topics_searched": ["recent only"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1, f"ожидался 1 gap-таск, got {len(gaps)}"
assert gaps[0]["task"] == "Run curious agent research cycle", gaps[0]
print("TEST 2 PASS: свежая единственная тема → generic-задача (не долбим свежее)")

# --- Test 3: граница 12ч — 11ч исключается, 13ч остаётся ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "eleven hours", "timestamp": now - 11 * 3600, "sources": [{"url": "e"}]},
        {"topic": "thirteen hours", "timestamp": now - 13 * 3600, "sources": [{"url": "t"}]},
    ],
    "topics_searched": ["eleven hours", "thirteen hours"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1, f"ожидался 1 gap-таск, got {len(gaps)}"
assert gaps[0]["task"] == "Run curious agent research cycle for topic: thirteen hours", gaps[0]
print("TEST 3 PASS: граница repeat-окна (11ч вне, 13ч внутри)")

# --- Test 4: ВСЕ темы свежие → generic без темы (консервативно) ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "x", "timestamp": now - 1 * 3600, "sources": [{"url": "x"}]},
        {"topic": "y", "timestamp": now - 4 * 3600, "sources": [{"url": "y"}]},
    ],
    "topics_searched": ["x", "y"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1 and gaps[0]["task"] == "Run curious agent research cycle", gaps
print("TEST 4 PASS: все темы свежие → generic-задача")

# --- Test 5: регрессия — находка без timestamp → generic ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "no-ts", "sources": [{"url": "n"}]}],
    "topics_searched": ["no-ts"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
gaps = gap_tasks(queue)
assert len(gaps) == 1 and gaps[0]["task"] == "Run curious agent research cycle", gaps
print("TEST 5 PASS: находка без timestamp → generic (регрессия)")

# --- Test 6: run_next пробрасывает ВЫЖИВШУЮ тему (15ч) в curious_agent ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "survivor topic", "timestamp": now - 15 * 3600, "sources": [{"url": "s"}]}],
    "topics_searched": ["survivor topic"],
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
assert captured["cmd"][-2:] == ["topic", "survivor topic"], captured["cmd"]
print("TEST 6 PASS: run_next → curious_agent topic 'survivor topic'")

# --- Test 7: _pick_gap_topic unit — repeat_hours=0/None/negative выключают фильтр ---
dated = [
    # дубль-тема: самая старая находка (20ч), но re-research 3ч назад
    {"topic": "dup", "timestamp": now - 20 * 3600},
    {"topic": "dup", "timestamp": now - 3 * 3600},
    {"topic": "clean", "timestamp": now - 15 * 3600},
]
# Фильтр выключен (0/отрицательный) → старое поведение: старейшая
# находка = 'dup' (минимальный epoch 20ч)
assert q._pick_gap_topic(dated, now, repeat_hours=0) == "dup"
assert q._pick_gap_topic(dated, now, repeat_hours=-5) == "dup"
# None = default 12ч → 'dup' исключена (новейшая 3ч < 12ч) → 'clean' (15ч)
assert q._pick_gap_topic(dated, now, repeat_hours=None) == "clean"
assert q._pick_gap_topic(dated, now) == "clean"
assert q._pick_gap_topic([], now) is None
# Без дублей (все находки > 12ч) → старейшая как раньше
dated_old = [{"topic": "a", "timestamp": now - 20 * 3600},
             {"topic": "b", "timestamp": now - 13 * 3600}]
assert q._pick_gap_topic(dated_old, now) == "a"
print("TEST 7 PASS: _pick_gap_topic unit (repeat=0/None/negative/дубли/пусто)")

# --- Test 8: регрессия — stale_topics блокирует knowledge_gap ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "very stale", "timestamp": now - 100 * 3600, "sources": [{"url": "v"}]},
        {"topic": "mid", "timestamp": now - 15 * 3600, "sources": [{"url": "m"}]},
    ],
    "topics_searched": ["very stale", "mid"],
    "last_search": now - 100 * 3600,
})
queue = q.build_queue()
assert not gap_tasks(queue), "gap-задача не должна создаваться при stale_topics"
print("TEST 8 PASS: stale_topics блокирует knowledge_gap (регрессия)")

print("\nALL TESTS PASS (8)")
