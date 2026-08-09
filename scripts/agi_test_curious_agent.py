#!/usr/bin/env python3
"""Standalone-тесты полного цикла agi_curious_agent (цикл 09.08).

Покрытие: load_knowledge (missing/broken/кривые типы), save_knowledge
(ротация findings 50 / topics_searched 100), кулдаун 1ч (skip/force/override),
полный цикл research (ok/no_new, last_search, findings), парсеры DDG
(html/lite/api), get_active_topics (триггеры из bridge, дефолт-фолбэк),
search_knowledge (тема/snippet/пусто), очистка отравленных записей.
Без сети: web_search_standalone и _load_bridge_context мокаются.
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
ca.SEARCH_DELAY = 0.0
ca.FALLBACK_DELAY = 0.0

OK_SOURCE = {"title": "t", "url": "https://e.x", "snippet": "s"}


def write(data: dict):
    ca.KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ca.KNOWLEDGE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def load() -> dict:
    return json.loads(ca.KNOWLEDGE_FILE.read_text())


def fake_search_ok(query):
    return [{"title": query[:20], "url": "https://ok.example", "snippet": "good"}]


def fake_search_fail(query):
    return [{"error": "all sources failed"}]


# --- 1. load_knowledge: missing / broken JSON / кривые типы (RED: normalization) ---
d = ca.load_knowledge()
assert d["findings"] == [] and d["topics_searched"] == [] and d["last_search"] == 0, d
print("TEST 1 PASS: load_knowledge на отсутствующем файле → дефолт")

ca.KNOWLEDGE_FILE.write_text("{broken json")  # сырой битый JSON (не через write: dumps сделал бы строку)
d = ca.load_knowledge()
assert d["findings"] == [] and d["topics_searched"] == [], d
print("TEST 2 PASS: load_knowledge на битом JSON → дефолт")

ca.KNOWLEDGE_FILE.write_text('"top-level string"')  # валидный JSON, но НЕ dict
d = ca.load_knowledge()
assert d["findings"] == [] and d["topics_searched"] == [], d
print("TEST 2b PASS: load_knowledge на JSON-строке → дефолт (раньше TypeError)")

write({"findings": None, "topics_searched": None, "last_search": None})
d = ca.load_knowledge()
assert d["findings"] == [] and d["topics_searched"] == [] and d["last_search"] == 0, d
print("TEST 3 PASS: load_knowledge нормализует null-поля (раньше TypeError в run_research)")

# --- 2. save_knowledge: ротация findings (50) и topics_searched (100) ---
write({})
big = {"findings": [{"topic": f"t{i}", "sources": [OK_SOURCE]} for i in range(60)],
       "topics_searched": [f"t{i}" for i in range(120)], "last_search": 0}
ca.save_knowledge(big)
d = load()
assert len(d["findings"]) == 50, len(d["findings"])
assert d["findings"][0]["topic"] == "t10", d["findings"][0]["topic"]  # последние 50
assert len(d["topics_searched"]) == 100, len(d["topics_searched"])
assert d["topics_searched"][0] == "t20", d["topics_searched"][0]
print("TEST 4 PASS: save_knowledge ротация 50/100, хранятся последние")

write({"findings": None, "topics_searched": None, "last_search": 0})
ca.save_knowledge({"findings": None, "topics_searched": None, "last_search": 0})
d = load()
assert d["findings"] == [] and d["topics_searched"] == [], d
print("TEST 5 PASS: save_knowledge переживает None-поля (раньше TypeError)")

# --- 3. Кулдаун: skip при недавнем поиске, force/override обходят ---
write({"findings": [], "topics_searched": [], "last_search": time.time()})
ca.web_search_standalone = fake_search_ok
res = ca.run_research()
assert res["status"] == "skipped", res
assert "findings_count" in res
print("TEST 6 PASS: кулдаун 1ч → skipped")

res = ca.run_research(force=True)
assert res["status"] == "ok", res
print("TEST 7 PASS: force обходит кулдаун")

write({"findings": [], "topics_searched": [], "last_search": time.time()})
res = ca.run_research(topics_override=["directed X"])
assert res["status"] == "ok" and "directed X" in res["topics_researched"], res
print("TEST 8 PASS: topics_override обходит кулдаун")

# --- 4. Полный цикл: ok / no_new / last_search / отсутствие дублей ---
write({"findings": [], "topics_searched": [], "last_search": 0})
ca._load_bridge_context = lambda: {"last_task": "fix the bug now"}
res = ca.run_research(force=True)
assert res["status"] == "ok", res
d = load()
assert d["last_search"] > 0
assert len(d["findings"]) == 1 and len(d["topics_searched"]) == 1
assert d["topics_searched"] == [d["findings"][0]["topic"]]
print("TEST 9 PASS: полный цикл research → finding + searched + last_search")

# directed re-research той же темы: findings не растут (дедуп замены)
topic = d["findings"][0]["topic"]
res = ca.run_research(force=True, topics_override=[topic])
d = load()
assert res["status"] == "ok"
assert len(d["findings"]) == 1, len(d["findings"])
assert d["topics_searched"].count(topic) == 1, d["topics_searched"]
print("TEST 10 PASS: re-research темы → 1 finding, без дублей в searched")

# --- 5. Провал поиска → no_new, тема НЕ помечена исследованной ---
write({"findings": [], "topics_searched": [], "last_search": 0})
ca.web_search_standalone = fake_search_fail
res = ca.run_research(force=True, topics_override=["bad topic"])
d = load()
assert res["status"] == "no_new", res
assert "bad topic" not in d["topics_searched"]
print("TEST 11 PASS: провал поиска → no_new, retry возможен")

# --- 6. Очистка отравленных записей (topics_searched без findings) ---
write({"findings": [{"topic": "alive", "timestamp": time.time(),
                     "sources": [OK_SOURCE]}],
       "topics_searched": ["alive", "poisoned"], "last_search": 0})
ca.web_search_standalone = fake_search_ok
ca.run_research(force=True)
d = load()
assert "poisoned" not in d["topics_searched"], d["topics_searched"]
print("TEST 12 PASS: отравленная запись topics_searched вычищена")

# --- 7. Парсеры DDG (без сети) ---
html_ddg = ('<div class="result"><a class="result__a" href="https://a.b/c">'
            '<b>Title</b> Here</a><a class="result__snippet">Some <b>snippet</b></a></div>')
r = ca._extract_ddg_html(html_ddg)
assert len(r) == 1 and r[0]["url"] == "https://a.b/c" and r[0]["title"] == "Title Here"
assert r[0]["snippet"] == "Some snippet"
print("TEST 13 PASS: _extract_ddg_html (title со стрипом тегов, snippet)")

lite = ('<a rel="nofollow" href="//lite.example/x">Lite Title</a>'
        '<td class="result-snippet">Lite snippet</td>')
r = ca._extract_ddg_lite(lite)
assert r and r[0]["url"] == "https://lite.example/x", r
assert r[0]["title"] == "Lite Title" and r[0]["snippet"] == "Lite snippet", r
print("TEST 14 PASS: _extract_ddg_lite (// → https, snippet из td)")

api_json = json.dumps({
    "Heading": "H", "AbstractText": "abs", "AbstractURL": "https://api.example",
    "RelatedTopics": [
        {"Text": "rel", "FirstURL": "https://rel.example"},
        {"Topics": [{"Text": "sub", "FirstURL": "https://sub.example"}]},
    ],
})
r = ca._extract_ddg_api(api_json)
assert len(r) == 3, r
assert r[0]["url"] == "https://api.example" and r[2]["url"] == "https://sub.example"
r = ca._extract_ddg_api("not json")
assert r == []
print("TEST 15 PASS: _extract_ddg_api (Abstract + Related + nested Topics, битый JSON)")

# --- 8. get_active_topics: триггеры из bridge, дефолт-фолбэк ---
ca._load_bridge_context = lambda: {"last_task": "fix crash timeout"}
topics = ca.get_active_topics()
assert topics and any("fix crash timeout" in t for t in topics), topics
print("TEST 16 PASS: get_active_topics по триггеру 'fix' из last_task")

ca._load_bridge_context = lambda: {"last_error": "connection refused to db"}
topics = ca.get_active_topics()
assert topics and any("connection refused" in t for t in topics), topics
print("TEST 17 PASS: get_active_topics по last_error")

ca._load_bridge_context = lambda: {}
topics = ca.get_active_topics()
assert topics and topics[0] in ca.DEFAULT_TOPICS, topics
assert len(topics) <= 3
print("TEST 18 PASS: get_active_topics → дефолтные темы (≤3)")

# --- 9. search_knowledge: по теме / по snippet / пусто ---
write({"findings": [
    {"topic": "alpha topic", "sources": [{"title": "x", "snippet": "zzz"}]},
    {"topic": "beta", "sources": [{"title": "needle in hay", "snippet": "yyy"}]},
], "topics_searched": ["alpha topic", "beta"], "last_search": 0})
r = ca.search_knowledge("alpha")
assert len(r) == 1 and r[0]["topic"] == "alpha topic", r
r = ca.search_knowledge("needle")
assert len(r) == 1 and r[0]["topic"] == "beta", r
r = ca.search_knowledge("nothing-here")
assert r == []
print("TEST 19 PASS: search_knowledge (тема, snippet, пусто)")

# --- 10. research_topic: структура результата ---
ca.web_search_standalone = fake_search_ok
res = ca.research_topic("some topic")
assert res["topic"] == "some topic" and res["timestamp"] > 0
assert res["sources"][0]["url"] == "https://ok.example"
print("TEST 20 PASS: research_topic структура")

print("\nALL TESTS PASS (20)")
