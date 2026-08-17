#!/usr/bin/env python3
"""agi_test_memory_supersede.py — тесты Temporal Supersession (цикл 37).

SELF_IMPROVE_2026-08-16 #2 (MemClaw): при перезаписи факта старый value
НЕ теряется — сохраняется в append-only memory_history как superseded
(provenance: что было, когда, чем заменено). Защита от provenance collapse.

Покрытие: история при value-изменении, отсутствие истории при одинаковом
value / новом ключе / тир-апгрейде, порядок newest-first, limit, фильтр по
key, невалидные входы, статистика, переживание eviction (retain_memory),
интеграция get_report.
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_context_store as cs

TMP = tempfile.mkdtemp(prefix="agi_supersede_test_")
cs.DB_PATH = Path(TMP) / "test.db"

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def _history_rows(key=None):
    with cs._get_conn() as conn:
        if key is None:
            rows = conn.execute(
                "SELECT key, old_value, old_tier, new_value, superseded_at"
                " FROM memory_history ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, old_value, old_tier, new_value, superseded_at"
                " FROM memory_history WHERE key = ? ORDER BY id", (key,)
            ).fetchall()
    return [dict(r) for r in rows]


def test_no_history_on_new_key():
    print("новый ключ — история пуста")
    added = cs.store_memory("k_new", "v1", "short")
    check("store вернул True", added is True)
    check("истории нет", len(_history_rows("k_new")) == 0)


def test_no_history_on_same_value():
    print("тот же value — не supersession")
    cs.store_memory("k_same", "v1", "short")
    added = cs.store_memory("k_same", "v1", "short")
    check("store вернул False (обновление)", added is False)
    check("истории нет", len(_history_rows("k_same")) == 0)


def test_history_on_value_change():
    print("смена value — старый факт в истории")
    cs.store_memory("k_swap", "old_value", "short")
    cs.store_memory("k_swap", "new_value", "short")
    rows = _history_rows("k_swap")
    check("1 строка истории", len(rows) == 1)
    r = rows[0]
    check("old_value сохранён", r["old_value"] == "old_value")
    check("new_value как provenance", r["new_value"] == "new_value")
    check("tier старого сохранён", r["old_tier"] == "short")
    check("superseded_at проставлен", r["superseded_at"] > 0)
    cur = cs.get_memory("k_swap")
    check("текущее значение обновлено", cur["value"] == "new_value")


def test_multiple_updates_newest_first():
    print("несколько перезаписей — newest-first, все версии сохранены")
    for v in ("v1", "v2", "v3", "v4"):
        cs.store_memory("k_multi", v, "short")
    hist = cs.get_memory_history("k_multi")
    check("3 superseded версии (4 store = 3 перезаписи)", len(hist) == 3)
    check("новейшая первая", hist[0]["old_value"] == "v3")
    check("самая старая последняя", hist[-1]["old_value"] == "v1")
    check("поля ключа", all(h["key"] == "k_multi" for h in hist))


def test_filter_and_limit():
    print("фильтр по key и limit")
    cs.store_memory("k_a", "1", "short")
    cs.store_memory("k_a", "2", "short")
    cs.store_memory("k_b", "1", "short")
    cs.store_memory("k_b", "2", "short")
    cs.store_memory("k_b", "3", "short")
    only_a = cs.get_memory_history("k_a")
    check("фильтр k_a: 1 строка", len(only_a) == 1 and only_a[0]["key"] == "k_a")
    lim = cs.get_memory_history(limit=1)
    check("limit=1: 1 строка", len(lim) == 1)
    missing = cs.get_memory_history("k_nope")
    check("нет ключа → []", missing == [])
    empty = cs.get_memory_history()
    check("всего строк ≥ 3", len(empty) >= 3)


def test_tier_promote_no_history():
    print("тир-апгрейд без смены value — без истории")
    cs.store_memory("k_tier", "fact", "short")
    cs.store_memory("k_tier", "fact", "medium")
    check("истории нет", len(_history_rows("k_tier")) == 0)


def test_invalid_inputs_no_history():
    print("невалидные входы — без истории")
    before = len(_history_rows("k_iv"))
    check("пустой key → False", cs.store_memory("  ", "x", "short") is False)
    check("None value → False", cs.store_memory("k_iv", None, "short") is False)
    check("bad tier → False", cs.store_memory("k_iv", "x", "ultra") is False)
    check("история не выросла", len(_history_rows("k_iv")) == before)


def test_history_stats():
    print("memory_history_stats")
    st = cs.memory_history_stats()
    check("total > 0", st["total"] > 0)
    check("keys > 0", st["keys"] > 0)
    check("keys ≤ total", st["keys"] <= st["total"])


def test_history_survives_eviction():
    print("provenance переживает eviction (append-only)")
    cs.store_memory("k_evict", "old", "long")
    cs.store_memory("k_evict", "new", "long")
    # принудительная эвикция long-тира: last_access=0, access_count=0 < 5
    res = cs.retain_memory(max_long=0, long_ttl_days=1,
                           min_long_accesses=5, medium_ttl_days=0)
    check("эвикция сработала", res["evicted_stale"] >= 1)
    check("get_memory → None", cs.get_memory("k_evict") is None)
    hist = cs.get_memory_history("k_evict")
    check("история сохранилась", len(hist) == 1)
    check("old_value на месте", hist[0]["old_value"] == "old")


def test_report_includes_history():
    print("get_report содержит superseded-секцию")
    cs.store_memory("k_report", "a", "short")
    cs.store_memory("k_report", "b", "short")  # создаём supersession
    report = cs.get_report()
    check("упоминание Superseded", "Superseded" in report)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
