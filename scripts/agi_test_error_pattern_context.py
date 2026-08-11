#!/usr/bin/env python3
"""agi_test_error_pattern_context.py — тесты контекст-предсказания (цикл 15).

Покрывает v4 error_pattern_learner: предсказание по КОНТЕКСТУ —
пары со-встречаемостей паттернов из истории сканов (grow point
SELF_IMPROVE_2026-08-11: «предсказание по контексту, не только по тексту»).

Секции:
1. _learn_cooccurrences: симметричные пары + порог min_pairs
2. _learn_cooccurrences: записи без "patterns" пропускаются без падения
3. predict_companions: предсказывает парный паттерн по текущим матчам
4. predict_companions: уже присутствующие паттерны исключаются
5. predict_companions: пустые входы -> []
6. predict_companions: лимит max_companions + сортировка по co_score
7. update_patterns: cooccurrences/companions персистятся в JSON (интеграция)
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_error_pattern_learner as epl

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def hist(pats_list):
    return [{"timestamp": time.time(), "error_count": 1, "patterns": p} for p in pats_list]


def main():
    print("== 1. _learn_cooccurrences builds symmetric pairs with threshold ==")
    d = {"history": hist([
        {"a": 1, "b": 2}, {"a": 1, "b": 1}, {"a": 1, "b": 3},   # (a,b) x3
        {"b": 1, "c": 1}, {"b": 2, "c": 1},                     # (b,c) x2
        {"a": 1},                                               # a solo
    ])}
    co = epl._learn_cooccurrences(d, min_pairs=2)
    check("pair (a,b) counted 3", co.get("a", {}).get("b") == 3, f"-> {co.get('a')}")
    check("symmetric (b,a) = 3", co.get("b", {}).get("a") == 3, f"-> {co.get('b')}")
    check("pair (b,c) counted 2", co.get("b", {}).get("c") == 2)
    check("no pair (a,c)", co.get("a", {}).get("c") is None)
    co3 = epl._learn_cooccurrences(d, min_pairs=3)
    check("min_pairs=3 keeps (a,b) only", co3.get("b", {}).get("a") == 3
          and co3.get("b", {}).get("c") is None, f"-> {co3.get('b')}")

    print("== 2. entries without 'patterns' are skipped ==")
    d_bad = {"history": [
        {"timestamp": time.time(), "error_count": 0},            # legacy entry
        {"timestamp": time.time(), "error_count": 1, "patterns": {"x": 1, "y": 1}},
        {"timestamp": time.time(), "error_count": 1, "patterns": None},  # corrupted
    ]}
    co_bad = epl._learn_cooccurrences(d_bad, min_pairs=1)
    check("no crash, pair (x,y) found", co_bad.get("x", {}).get("y") == 1, f"-> {co_bad}")

    print("== 3. predict_companions predicts paired pattern ==")
    d3 = {
        "history": hist([{"a": 1, "b": 1}, {"a": 1, "b": 1}, {"a": 1, "b": 1}]),
        "cooccurrences": {"a": {"b": 3}},
    }
    comps = epl.predict_companions(d3, {"a": 1})
    check("predicts b", len(comps) == 1 and comps[0]["pattern"] == "b", f"-> {comps}")
    check("co_score = 3", comps and comps[0]["co_score"] == 3)
    check("message present", comps and "b" in comps[0]["message"])

    print("== 4. already-present patterns excluded ==")
    comps4 = epl.predict_companions(d3, {"a": 1, "b": 2})
    check("empty when both present", comps4 == [], f"-> {comps4}")

    print("== 5. empty inputs -> [] ==")
    check("no current matches", epl.predict_companions(d3, {}) == [])
    check("no history/cooccurrences", epl.predict_companions({"history": []}, {"a": 1}) == [])

    print("== 6. max_companions limit + score ordering ==")
    d6 = {
        "history": hist([{"a": 1, "b": 1, "c": 1, "d": 1, "e": 1},
                         {"a": 1, "b": 1, "c": 1, "d": 1},
                         {"a": 1, "b": 1, "c": 1}]),
        # b:3, c:3, d:2, e:1
        "cooccurrences": {"a": {"b": 3, "c": 3, "d": 2, "e": 1}},
    }
    comps6 = epl.predict_companions(d6, {"a": 1}, max_companions=3)
    check("top-3 by score", [c["pattern"] for c in comps6] == ["b", "c", "d"],
          f"-> {[c['pattern'] for c in comps6]}")
    check("scores desc", [c["co_score"] for c in comps6] == [3, 3, 2],
          f"-> {[c['co_score'] for c in comps6]}")

    print("== 7. update_patterns persists cooccurrences/companions (integration) ==")
    tmp = Path(tempfile.mkdtemp(prefix="epl_ctx_"))
    epl.LOG_DIR = tmp
    epl.SUPERVISOR_LOG = tmp / "nonexistent.md"
    epl.SESSION_DIR = tmp / "sessions"
    epl.PATTERNS_FILE = tmp / "error_patterns.json"
    log = tmp / "errors.log"
    # run 1+2: оба паттерна вместе -> пара (connection_refused, gateway_timeout) x2
    log.write_text("ERROR connection refused on port 8080\nERROR Gateway Timeout upstream\n")
    r1 = epl.update_patterns()
    r2 = epl.update_patterns()
    # run 3: только connection_refused -> gateway_timeout должен быть предсказан
    log.write_text("ERROR connection refused on port 8080\n")
    r3 = epl.update_patterns()
    saved = json.loads(epl.PATTERNS_FILE.read_text())
    check("cooccurrences persisted", "cooccurrences" in saved
          and saved["cooccurrences"].get("connection_refused", {}).get("gateway_timeout") == 2,
          f"-> {saved.get('cooccurrences', {}).get('connection_refused')}")
    check("companions key in result", "companions" in r3, f"-> keys: {list(r3.keys())}")
    check("gateway_timeout predicted in run 3",
          any(c["pattern"] == "gateway_timeout" for c in r3["companions"]),
          f"-> {r3['companions']}")
    check("run 2 had no companions (both present)",
          all(c["pattern"] != "gateway_timeout" for c in r2["companions"]),
          f"-> {r2['companions']}")
    check("companions persisted in JSON", saved.get("companions") == r3["companions"])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
