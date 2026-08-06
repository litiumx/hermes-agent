#!/usr/bin/env python3
"""Юнит-тест directed-topic интеграции: queue runner → curious_agent topic CLI (06.08.2026)."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_self_directed_queue as q
import agi_curious_agent as ca

TMP = Path(tempfile.mkdtemp())
q.BRIDGE_FILE = TMP / "bridge.json"
q.PATTERNS_FILE = TMP / "error_patterns.json"
q.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"
q.QUEUE_FILE = TMP / "task_queue.json"
ca.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def clean():
    for p in (q.BRIDGE_FILE, q.PATTERNS_FILE, q.KNOWLEDGE_FILE, q.QUEUE_FILE):
        if p.exists():
            p.unlink()


# --- Test 1: run_next пробрасывает directed-topic в cmd curious_agent ---
clean()
# Прогреваем кулдаун дефолтных задач (health check/self-improve), иначе
# они (prio 40/35) обгоняют directed-тему. Возраст 200ч → prio 46.
now = time.time()
write(q.QUEUE_FILE, {"history": [
    {"task": "Run system health check and proactive scan", "ts": now - 60},
    {"task": "Run self-improvement cycle (self_improve.py)", "ts": now - 60},
]})
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "stale X", "timestamp": now - 200 * 3600, "sources": [{"url": "x"}]}],
    "topics_searched": ["stale X"],
    "last_search": now - 1,
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
assert captured["cmd"][-2:] == ["topic", "stale X"], captured["cmd"]
print("TEST 1 PASS: run_next передаёт directed-topic в agi_curious_agent.py")

# --- Test 2: generic-задача НЕ получает topic-аргумент ---
clean()
write(q.KNOWLEDGE_FILE, {
    "findings": [{"topic": "fresh", "timestamp": time.time() - 1 * 3600, "sources": [{"url": "f"}]}],
    "topics_searched": ["fresh"],
    "last_search": time.time() - 100 * 3600,  # давно → knowledge_gap
})
captured.clear()
subprocess.run = fake_run
res = q.run_next()
subprocess.run = orig_run
assert res["status"] == "done", res
assert "topic" not in captured["cmd"], captured["cmd"]
print("TEST 2 PASS: generic research-задача без topic-аргумента (регрессия)")

# --- Test 3: run_research(topics_override) ре-исследует searched-тему и заменяет находку ---
clean()
write(ca.KNOWLEDGE_FILE, {
    "findings": [{"topic": "stale X", "timestamp": time.time() - 48 * 3600, "sources": [{"url": "old"}]}],
    "topics_searched": ["stale X"],
    "last_search": time.time() - 1,
})


def fake_search(query):
    return [{"title": "fresh", "url": "https://new.example", "snippet": "new data"}]


ca.web_search_standalone = fake_search
res = ca.run_research(force=True, topics_override=["stale X"])
assert res["status"] == "ok" and res["topics_researched"] == ["stale X"], res
data = json.loads(ca.KNOWLEDGE_FILE.read_text())
assert len(data["findings"]) == 1, data["findings"]  # старая заменена, не дубли
assert data["findings"][0]["sources"][0]["url"] == "https://new.example"
assert data["findings"][0]["timestamp"] > time.time() - 60  # свежая
print("TEST 3 PASS: directed ре-исследование searched-темы, старая находка заменена")

# --- Test 4: без override — прежнее поведение (searched-темы пропускаются) ---
clean()
write(ca.KNOWLEDGE_FILE, {
    "findings": [{"topic": "done Y", "timestamp": time.time() - 1, "sources": [{"url": "y"}]}],
    "topics_searched": ["done Y"],
    "last_search": time.time() - 1,
})
res = ca.run_research(force=True)  # force, но БЕЗ override
# Ключевая регрессия: searched-тема "done Y" НЕ ре-исследуется без override.
# (При пустом bridge-контексте агент может взять дефолтную тему — это
# прежнее поведение, не регрессия.)
assert "done Y" not in res.get("topics_researched", []), res
data = json.loads(ca.KNOWLEDGE_FILE.read_text())
# Находка "done Y" не тронута (фолбэк добавил отдельную дефолтную тему)
done = [f for f in data["findings"] if f.get("topic") == "done Y"]
assert len(done) == 1 and done[0]["sources"][0]["url"] == "y", data["findings"]
print("TEST 4 PASS: без override searched-темы по-прежнему пропускаются")

# --- Test 5: CLI-режим topic парсит аргумент и форсит re-research ---
calls = {}


def fake_run_research(force=False, topics_override=None):
    calls["force"] = force
    calls["topics_override"] = topics_override
    return {"status": "ok", "topics_researched": topics_override}


orig_rr = ca.run_research
ca.run_research = fake_run_research
try:
    ca.main(["topic", "hello world"])
finally:
    ca.run_research = orig_rr
assert calls.get("force") is True, calls
assert calls.get("topics_override") == ["hello world"], calls
print("TEST 5 PASS: CLI 'topic <тема>' → run_research(force=True, topics_override=[тема])")

# --- Test 6: CLI topic без аргумента — usage и exit 1 ---
try:
    ca.main(["topic"])
    raise AssertionError("ожидался SystemExit")
except SystemExit as e:
    assert e.code == 1, e.code
print("TEST 6 PASS: CLI topic без темы → SystemExit(1) с usage")

print("\nALL TESTS PASS")
