#!/usr/bin/env python3
"""Юнит-тест унификации выбора re-research тем (цикл 27, grow point 23-26).

Проблема: два параллельных пути «что ре-исследовать» с разной семантикой
возраста. stale_topics строились ПОСТРОЧНО по findings — дубли находок темы
плодили дубли задач, и тема, ре-исследованная 2ч назад, всё равно считалась
stale, если старая находка осталась. knowledge_gap шёл через _pick_gap_topic
(max-ts темы + repeat-фильтр). Решение: единый источник правды по возрасту —
_topic_research_times (последнее исследование темы = max ts её находок),
поверх него _pick_stale_topics (dedup, возраст по последнему исследованию).

Покрытие: _topic_research_times (малфомы/дубли/границы ts), _pick_stale_topics
(пусто/dedup/re-research спасает/cap/сортировка/параметры), load_state
интеграция (дубли → 1 запись, свежий re-research исключает), build_queue
(1 задача на тему, приоритет по возрасту, gap-блокировка регрессия),
_pick_gap_topic регрессия через общее ядро. Без сети.
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

HOUR = 3600
NOW = time.time()


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


def warm_defaults():
    """Прогреть кулдаун дефолтных задач (health check/self-improve не конкурируют)."""
    write(q.QUEUE_FILE, {"history": [
        {"task": "Run system health check and proactive scan", "ts": NOW - 60},
        {"task": "Run self-improvement cycle (self_improve.py)", "ts": NOW - 60},
    ]})


def stale_tasks(queue):
    return [t for t in queue if t.get("source") == "stale_topic"]


# --- 1. _topic_research_times: базовый ---
times = q._topic_research_times([])
assert times == {}, times
times = q._topic_research_times("not a list")
assert times == {}, times
times = q._topic_research_times([{"topic": "a", "timestamp": NOW - 10 * HOUR}])
assert times == {"a": {"last": NOW - 10 * HOUR, "oldest": NOW - 10 * HOUR}}, times
print("TEST 1 PASS: _topic_research_times базовый (пусто/не-список/одиночная)")

# --- 2. _topic_research_times: дубли — last=max, oldest=min ---
times = q._topic_research_times([
    {"topic": "dup", "timestamp": NOW - 30 * HOUR},
    {"topic": "dup", "timestamp": NOW - 2 * HOUR},
    {"topic": "dup", "timestamp": NOW - 15 * HOUR},
])
assert times["dup"]["last"] == NOW - 2 * HOUR, times
assert times["dup"]["oldest"] == NOW - 30 * HOUR, times
print("TEST 2 PASS: _topic_research_times дубли (last=max/oldest=min)")

# --- 3. _topic_research_times: малфомы пропускаются ---
times = q._topic_research_times([
    "garbage",
    {"timestamp": NOW - 5 * HOUR},            # нет topic
    {"topic": "  ", "timestamp": NOW - 5 * HOUR},  # пустой topic
    {"topic": "no-ts"},                       # нет timestamp
    {"topic": "zero-ts", "timestamp": 0},     # ts=0 — малфом (эпоха, не время)
    {"topic": "neg-ts", "timestamp": -100},   # отрицательный ts — малфом
    {"topic": "str-ts", "timestamp": "abc"},  # не число
    {"topic": "ok", "timestamp": NOW - 5 * HOUR},
])
assert times == {"ok": {"last": NOW - 5 * HOUR, "oldest": NOW - 5 * HOUR}}, times
print("TEST 3 PASS: _topic_research_times малфомы (не-dict/без topic/ts<=0/не число)")

# --- 4. _pick_stale_topics: пусто / ничего stale ---
assert q._pick_stale_topics([], NOW) == []
assert q._pick_stale_topics(
    [{"topic": "fresh", "timestamp": NOW - 2 * HOUR}], NOW) == []
print("TEST 4 PASS: _pick_stale_topics пусто/свежие → []")

# --- 5. _pick_stale_topics: dedup — дубли находок = ОДНА запись (ключевой фикс) ---
stale = q._pick_stale_topics([
    {"topic": "dup-stale", "timestamp": NOW - 100 * HOUR},
    {"topic": "dup-stale", "timestamp": NOW - 90 * HOUR},
    {"topic": "dup-stale", "timestamp": NOW - 80 * HOUR},
], NOW)
assert len(stale) == 1, f"дубли тем плодят дубли записей: {stale}"
assert stale[0]["topic"] == "dup-stale"
# возраст по ПОСЛЕДНЕМУ исследованию (80ч), не по самой старой находке (100ч)
assert abs(stale[0]["age_hours"] - 80.0) < 1.0, stale
print("TEST 5 PASS: _pick_stale_topics dedup, возраст по последнему исследованию")

# --- 6. _pick_stale_topics: свежий re-research спасает тему от stale ---
stale = q._pick_stale_topics([
    {"topic": "rescued", "timestamp": NOW - 30 * HOUR},   # старая находка осталась
    {"topic": "rescued", "timestamp": NOW - 2 * HOUR},    # re-research 2ч назад
    {"topic": "old-one", "timestamp": NOW - 40 * HOUR},
], NOW)
topics = [s["topic"] for s in stale]
assert "rescued" not in topics, f"re-research 2ч назад не должен быть stale: {stale}"
assert "old-one" in topics, stale
print("TEST 6 PASS: свежий re-research исключает тему (старая находка не в счёт)")

# --- 7. _pick_stale_topics: сортировка по возрасту (самые старые первыми) ---
stale = q._pick_stale_topics([
    {"topic": "mid", "timestamp": NOW - 30 * HOUR},
    {"topic": "oldest", "timestamp": NOW - 100 * HOUR},
    {"topic": "border", "timestamp": NOW - 25 * HOUR},
], NOW)
assert [s["topic"] for s in stale] == ["oldest", "mid", "border"], stale
print("TEST 7 PASS: _pick_stale_topics сортировка oldest-first")

# --- 8. _pick_stale_topics: cap и параметры ---
findings = [{"topic": f"t{i}", "timestamp": NOW - (30 + i) * HOUR} for i in range(5)]
stale = q._pick_stale_topics(findings, NOW, max_topics=3)
assert len(stale) == 3, stale
assert [s["topic"] for s in stale] == ["t4", "t3", "t2"], stale
# custom stale_hours: порог 100ч — ничего stale
assert q._pick_stale_topics(findings, NOW, stale_hours=100) == []
# stale_hours <= 0 / не число → дефолт RESEARCH_STALE_HOURS (24ч) — не «всё stale»
assert len(q._pick_stale_topics(findings, NOW, stale_hours=0, max_topics=10)) == len(findings)
assert len(q._pick_stale_topics(findings, NOW, stale_hours="x", max_topics=10)) == len(findings)
# граница: ровно 24ч — НЕ stale (строгое >), 24ч+1с — stale
assert q._pick_stale_topics(
    [{"topic": "edge", "timestamp": NOW - q.RESEARCH_STALE_HOURS * HOUR}], NOW) == []
assert q._pick_stale_topics(
    [{"topic": "edge", "timestamp": NOW - q.RESEARCH_STALE_HOURS * HOUR - 1}], NOW)
print("TEST 8 PASS: _pick_stale_topics cap/порог/граница/дефолт")

# --- 9. load_state: дубли находок → ОДНА stale-запись ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "dup real", "timestamp": NOW - 50 * HOUR, "sources": [{"url": "d1"}]},
        {"topic": "dup real", "timestamp": NOW - 49 * HOUR, "sources": [{"url": "d2"}]},
    ],
    "last_search": NOW - 100 * HOUR,
})
state = q.load_state()
assert len(state["stale_topics"]) == 1, state["stale_topics"]
assert state["stale_topics"][0]["topic"] == "dup real", state["stale_topics"]
print("TEST 9 PASS: load_state dedup — 1 stale-запись на тему")

# --- 10. load_state: свежий re-research убирает тему из stale ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "recently done", "timestamp": NOW - 50 * HOUR, "sources": [{"url": "r1"}]},
        {"topic": "recently done", "timestamp": NOW - 3 * HOUR, "sources": [{"url": "r2"}]},
        {"topic": "actually stale", "timestamp": NOW - 60 * HOUR, "sources": [{"url": "a"}]},
    ],
    "last_search": NOW - 100 * HOUR,
})
state = q.load_state()
topics = [s["topic"] for s in state["stale_topics"]]
assert "recently done" not in topics, state["stale_topics"]
assert "actually stale" in topics, state["stale_topics"]
print("TEST 10 PASS: load_state — re-research 3ч назад исключает тему из stale")

# --- 11. build_queue: 1 задача на тему (нет дублей), приоритет по возрасту ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [
        {"topic": "dup-again", "timestamp": NOW - 70 * HOUR, "sources": [{"url": "x"}]},
        {"topic": "dup-again", "timestamp": NOW - 68 * HOUR, "sources": [{"url": "y"}]},
        {"topic": "less-old", "timestamp": NOW - 26 * HOUR, "sources": [{"url": "z"}]},
    ],
    "last_search": NOW - 100 * HOUR,
})
queue = q.build_queue()
st = stale_tasks(queue)
assert len(st) == 2, f"ожидались 2 задачи (2 темы), got {len(st)}: {st}"
tasks_text = [t["task"] for t in st]
assert sum(1 for t in tasks_text if "dup-again" in t) == 1, tasks_text
dup_task = [t for t in st if "dup-again" in t["task"]][0]
less_task = [t for t in st if "less-old" in t["task"]][0]
assert dup_task["priority"] > less_task["priority"], (dup_task, less_task)
print("TEST 11 PASS: build_queue — 1 задача на тему, приоритет по возрасту")

# --- 12. Регрессия: stale_topics блокирует knowledge_gap ---
clean()
warm_defaults()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "very stale", "timestamp": NOW - 100 * HOUR,
                  "sources": [{"url": "v"}]}],
    "last_search": NOW - 100 * HOUR,
})
queue = q.build_queue()
assert not [t for t in queue if t.get("source") == "knowledge_gap"], queue
print("TEST 12 PASS: stale_topics блокирует knowledge_gap (регрессия)")

# --- 13. Регрессия: _pick_gap_topic через общее ядро ---
dated = [
    {"topic": "dup", "timestamp": NOW - 20 * HOUR},
    {"topic": "dup", "timestamp": NOW - 3 * HOUR},
    {"topic": "clean", "timestamp": NOW - 15 * HOUR},
]
assert q._pick_gap_topic(dated, NOW) == "clean", "repeat-фильтр сломан"
assert q._pick_gap_topic(dated, NOW, repeat_hours=0) == "dup", "repeat=0 выкл"
assert q._pick_gap_topic([], NOW) is None
assert q._pick_gap_topic(
    [{"topic": "a", "timestamp": NOW - 20 * HOUR},
     {"topic": "b", "timestamp": NOW - 13 * HOUR}], NOW) == "a"
print("TEST 13 PASS: _pick_gap_topic регрессия (дубли/фильтр/пусто/старейшая)")

print("\nALL TESTS PASS (13)")
