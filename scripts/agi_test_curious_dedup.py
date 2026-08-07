#!/usr/bin/env python3
"""Юнит-тест дедупа topics_searched в agi_curious_agent (цикл 07.08, фикс №2).

Баг: directed re-research одной stale-темы добавлял тему в topics_searched
каждый раз → список забивался дублями (кап 100 уходит на 1 тему, отчёт
"Тем исследовано" врёт). Теперь перед append старое вхождение удаляется.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_curious_agent as ca

TMP = Path(tempfile.mkdtemp())
ca.KNOWLEDGE_FILE = TMP / "curious_knowledge.json"


def write(data: dict):
    ca.KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ca.KNOWLEDGE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def load() -> dict:
    return json.loads(ca.KNOWLEDGE_FILE.read_text())


def fake_search(query):
    return [{"title": "fresh", "url": "https://new.example", "snippet": "new data"}]


# --- Test 1: 3 directed re-research одной темы → БЕЗ дублей в topics_searched ---
write({
    "findings": [{"topic": "stale X", "timestamp": time.time() - 48 * 3600,
                  "sources": [{"url": "old"}]}],
    "topics_searched": ["stale X"],
    "last_search": time.time() - 1,
})
ca.web_search_standalone = fake_search
for _ in range(3):
    res = ca.run_research(force=True, topics_override=["stale X"])
    assert res["status"] == "ok", res
data = load()
assert data["topics_searched"] == ["stale X"], data["topics_searched"]
print("TEST 1 PASS: 3 directed цикла → topics_searched без дублей")

# --- Test 2: разные темы добавляются в конец, старые не теряются ---
write({
    "findings": [{"topic": "A", "timestamp": time.time() - 1, "sources": [{"url": "a"}]}],
    "topics_searched": ["A"],
    "last_search": time.time() - 1,
})
ca.run_research(force=True, topics_override=["B"])
data = load()
# A осталась (в findings), B добавлена, дублей нет
assert data["topics_searched"] == ["A", "B"], data["topics_searched"]
print("TEST 2 PASS: новая тема добавляется без потери старых")

# --- Test 3: регрессия — обычный цикл (без override) не трогает searched ---
write({
    "findings": [{"topic": "done Y", "timestamp": time.time() - 1,
                  "sources": [{"url": "y"}]}],
    "topics_searched": ["done Y"],
    "last_search": time.time() - 1,
})
res = ca.run_research(force=True)  # force, но без override
assert "done Y" not in res.get("topics_researched", []), res
data = load()
# При пустом bridge-контексте агент может исследовать дефолтную тему —
# это прежнее поведение. Ключевое: "done Y" ровно один раз (без дублей).
assert data["topics_searched"].count("done Y") == 1, data["topics_searched"]
print("TEST 3 PASS: без override searched-темы пропускаются, дублей нет")

# --- Test 4: регрессия — провал поиска не помечает тему исследованной ---
write({
    "findings": [],
    "topics_searched": [],
    "last_search": 0,
})
ca.web_search_standalone = lambda q: [{"error": "all sources failed"}]
res = ca.run_research(force=True, topics_override=["bad topic"])
data = load()
assert res["status"] == "no_new", res
assert "bad topic" not in data["topics_searched"], data["topics_searched"]
print("TEST 4 PASS: провал поиска → тема НЕ в topics_searched (retry возможен)")

# --- Test 5: next_topic возвращается без исключений при пустом контексте ---
write({"findings": [], "topics_searched": [], "last_search": 0})
res = ca.run_research(force=True)
assert "next_topic" in res, res
print("TEST 5 PASS: next_topic присутствует, без исключений")

print("\nALL TESTS PASS")
