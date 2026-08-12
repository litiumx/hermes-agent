#!/usr/bin/env python3
"""Standalone-тесты stale-топиков agi_curious_agent (цикл 17, grow point 11.08).

Покрытие: _topic_score (пусто/источники/cap/snippet-бонус), get_stale_topics
(пусто/свежие/старые/сортировка/граница/битые записи), prune_stale_topics
(удаление stale+low-score, сохранение stale+high-score и свежих, идемпотентность),
get_report со stale-секцией. Без сети: только чистые функции.
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

DAY = 86400
NOW = time.time()


def write(data: dict):
    ca.KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ca.KNOWLEDGE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def finding(topic, age_days, n_sources, with_snippet=True):
    """Находка возрастом age_days: n_sources источников."""
    src = []
    for i in range(n_sources):
        s = {"title": f"t{i}", "url": f"https://e{i}.x"}
        if with_snippet:
            s["snippet"] = f"s{i}"
        src.append(s)
    return {"topic": topic, "timestamp": NOW - age_days * DAY, "sources": src}


# --- 1. _topic_score: пусто, источники, cap, snippet-бонус ---
assert ca._topic_score({"topic": "a", "timestamp": 0, "sources": []}) == 0.0
print("TEST 1 PASS: score пустой находки = 0")

assert ca._topic_score(finding("a", 1, 3)) == 3.5  # 3 источника + бонус за snippet
print("TEST 2 PASS: score = число источников +0.5 за snippet")

assert ca._topic_score(finding("a", 1, 10)) == 5.5  # cap 5 + бонус
print("TEST 3 PASS: score cap = 5 (+0.5 за snippet)")

assert ca._topic_score(finding("a", 1, 2, with_snippet=False)) == 2.0
assert ca._topic_score(finding("a", 1, 2, with_snippet=True)) == 2.5  # +0.5 плоский
print("TEST 4 PASS: snippet-бонус +0.5 плоский (не по-источниково)")

# --- 2. get_stale_topics ---
assert ca.get_stale_topics({}, max_age_days=30) == []
assert ca.get_stale_topics({"findings": []}, max_age_days=30) == []
print("TEST 5 PASS: пустые входы → []")

kb = {"findings": [finding("fresh", 1, 3), finding("old", 40, 2)]}
stale = ca.get_stale_topics(kb, max_age_days=30)
assert [s["topic"] for s in stale] == ["old"], stale
assert stale[0]["age_days"] == 40 and stale[0]["score"] == 2.5
print("TEST 6 PASS: stale = только старше max_age_days, с возрастом и score")

# Граница: ровно max_age_days → stale (>=, детерминизм)
kb = {"findings": [finding("border", 30, 1)]}
stale = ca.get_stale_topics(kb, max_age_days=30)
assert [s["topic"] for s in stale] == ["border"]
print("TEST 7 PASS: граница ровно max_age_days → stale")

# Сортировка: самый старый первым, при равном возрасте — меньший score первым
kb = {"findings": [
    finding("mid", 45, 4),
    finding("oldest", 90, 1),
    finding("newest_old", 35, 2),
]}
stale = ca.get_stale_topics(kb, max_age_days=30)
assert [s["topic"] for s in stale] == ["oldest", "mid", "newest_old"], stale
print("TEST 8 PASS: сортировка по возрасту desc")

kb = {"findings": [
    finding("high", 60, 5),
    finding("low", 60, 1),
]}
stale = ca.get_stale_topics(kb, max_age_days=30)
assert [s["topic"] for s in stale] == ["low", "high"], stale
print("TEST 9 PASS: при равном возрасте меньший score первым (кандидат на выброс)")

# Битые записи: без timestamp / не-dict → не stale (консервативно не трогаем)
kb = {"findings": [
    {"topic": "no_ts", "sources": []},
    "garbage",
    {"topic": "old", "timestamp": NOW - 100 * DAY, "sources": [{"title": "t", "url": "u"}]},
]}
stale = ca.get_stale_topics(kb, max_age_days=30)
assert [s["topic"] for s in stale] == ["old"], stale
print("TEST 10 PASS: записи без timestamp и мусор не считаются stale")

# --- 3. prune_stale_topics ---
# Удаляет только stale + score < min_score; сохраняет stale-ценные и свежие
write({"findings": [
    finding("stale_low", 70, 0),       # удалить (score 0 < 1)
    finding("stale_mid", 70, 2),       # удалить (score 2 >= 1? нет: min_score=3 → удалить)
    finding("stale_high", 70, 4),      # сохранить (score 4 >= 3)
    finding("fresh", 1, 0),            # сохранить (свежая)
], "topics_searched": ["stale_low", "stale_mid", "stale_high", "fresh"], "last_search": 0})
res = ca.prune_stale_topics(max_age_days=30, min_score=3.0)
assert res["removed"] == 2, res
assert res["kept"] == 2, res
kb = json.loads(ca.KNOWLEDGE_FILE.read_text())
assert [f["topic"] for f in kb["findings"]] == ["stale_high", "fresh"], kb["findings"]
# topics_searched тоже чистится от удалённых тем
assert kb["topics_searched"] == ["stale_high", "fresh"], kb["topics_searched"]
print("TEST 11 PASS: prune удаляет stale+low-score, чистит topics_searched")

# Идемпотентность: второй прогон ничего не удаляет
res2 = ca.prune_stale_topics(max_age_days=30, min_score=3.0)
assert res2["removed"] == 0 and res2["kept"] == 2, res2
print("TEST 12 PASS: prune идемпотентен")

# Пустая база / отсутствующий файл
ca.KNOWLEDGE_FILE.unlink(missing_ok=True)
res3 = ca.prune_stale_topics(max_age_days=30, min_score=1.0)
assert res3["removed"] == 0 and res3["kept"] == 0, res3
print("TEST 13 PASS: prune на пустой базе → 0/0")

# --- 4. get_report со stale-секцией ---
write({"findings": [
    finding("stale_cand", 60, 1),
    finding("fresh", 1, 2),
], "topics_searched": ["stale_cand", "fresh"], "last_search": NOW})
rep = ca.get_report()
assert "stale_cand" in rep and "Устаревших" in rep, rep
print("TEST 14 PASS: get_report показывает stale-кандидатов")

write({"findings": [finding("fresh", 1, 2)], "topics_searched": ["fresh"], "last_search": NOW})
rep = ca.get_report()
assert "Устаревших" in rep and "нет" in rep, rep
print("TEST 15 PASS: get_report без stale — секция с 'нет'")

print("\nALL 15 TESTS PASS (agi_test_curious_stale.py)")
