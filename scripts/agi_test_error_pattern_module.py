#!/usr/bin/env python3
"""agi_test_error_pattern_module.py — тесты модуль-контекста (цикл 16).

Покрывает v5 error_pattern_learner: контекст по МОДУЛЮ (grow point
SELF_IMPROVE_2026-08-11: «какой лог-файл/сервис дал ошибку — сузить
предсказания до сервиса»).

Секции:
1. scan_logs_by_source: разбивка матчей по источникам (лог-файлам)
2. scan_logs_by_source: агрегат scan_logs не изменился (backward compat)
3. _learn_module_pairs: пары ТОЛЬКО внутри одного источника + порог
4. _learn_module_pairs: записи без "sources" пропускаются без падения
5. predict_module_companions: предсказывает парный паттерн в том же модуле
6. predict_module_companions: присутствующие исключаются, пустые входы -> []
7. predict_module_companions: лимит + сортировка по co_score
8. update_patterns: sources/module_cooccurrences/module_companions персистятся
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


def hist_with_sources(pats_srcs_list):
    """История: [(patterns, sources), ...] -> записи history."""
    out = []
    for pats, srcs in pats_srcs_list:
        out.append({"timestamp": time.time(), "error_count": 1,
                    "patterns": pats, "sources": srcs})
    return out


def main():
    print("== 1. scan_logs_by_source splits matches by source file ==")
    tmp = Path(tempfile.mkdtemp(prefix="epl_mod_"))
    epl.LOG_DIR = tmp
    epl.SUPERVISOR_LOG = tmp / "nonexistent.md"
    epl.SESSION_DIR = tmp / "sessions"
    epl.SESSION_DIR.mkdir(exist_ok=True)
    epl.PATTERNS_FILE = tmp / "error_patterns.json"
    (tmp / "errors.log").write_text("ERROR connection refused on port 8080\n")
    (tmp / "gateway.log").write_text("ERROR Gateway Timeout upstream\n"
                                     "ERROR connection refused on port 9090\n")
    (tmp / "agent.log").write_text("INFO nothing wrong\n")
    by_src = epl.scan_logs_by_source({})
    check("errors.log has connection_refused",
          by_src.get("errors.log", {}).get("connection_refused") == 1,
          f"-> {by_src.get('errors.log')}")
    check("gateway.log has both patterns",
          by_src.get("gateway.log", {}).get("gateway_timeout") == 1
          and by_src.get("gateway.log", {}).get("connection_refused") == 1,
          f"-> {by_src.get('gateway.log')}")
    check("agent.log absent (no matches)",
          "agent.log" not in by_src, f"-> {list(by_src.keys())}")

    print("== 2. aggregate scan_logs unchanged (backward compat) ==")
    agg = epl.scan_logs({})
    check("aggregate counts sum across files",
          agg.get("connection_refused") == 2 and agg.get("gateway_timeout") == 1,
          f"-> {agg}")

    print("== 3. _learn_module_pairs: pairs only within same source ==")
    d3 = {"history": hist_with_sources([
        # gateway.log: (a,b) x3; errors.log: (a,c) x3; НО a и c в разных
        # источниках от b — пары b-c не должно быть
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
    ])}
    pairs = epl._learn_module_pairs(d3, min_pairs=2)
    check("pair (a,b) in module counted 3",
          pairs.get("gateway.log", {}).get("a", {}).get("b") == 3,
          f"-> {pairs.get('gateway.log')}")
    check("symmetric (b,a) = 3",
          pairs.get("gateway.log", {}).get("b", {}).get("a") == 3)
    # a в errors.log, b в gateway.log — в одном модуле не встречались
    d3b = {"history": hist_with_sources([
        ({"a": 1, "b": 1}, {"errors.log": {"a": 1}, "gateway.log": {"b": 1}}),
        ({"a": 1, "b": 1}, {"errors.log": {"a": 1}, "gateway.log": {"b": 1}}),
        ({"a": 1, "b": 1}, {"errors.log": {"a": 1}, "gateway.log": {"b": 1}}),
    ])}
    pairs_b = epl._learn_module_pairs(d3b, min_pairs=2)
    check("no cross-source pair (a,b)",
          pairs_b == {}, f"-> {pairs_b}")
    d3c = {"history": hist_with_sources([
        ({"a": 1, "b": 1}, {"errors.log": {"a": 1, "b": 1}}),
    ])}
    pairs_c = epl._learn_module_pairs(d3c, min_pairs=2)
    check("min_pairs=2 drops single co-occurrence",
          pairs_c == {}, f"-> {pairs_c}")

    print("== 4. entries without 'sources' skipped ==")
    d4 = {"history": [
        {"timestamp": time.time(), "error_count": 1, "patterns": {"x": 1, "y": 1}},
        {"timestamp": time.time(), "error_count": 0},
        {"timestamp": time.time(), "error_count": 1, "patterns": {"x": 1, "y": 1},
         "sources": {"errors.log": {"x": 1, "y": 1}}},
        {"timestamp": time.time(), "error_count": 1, "patterns": {"x": 1, "y": 1},
         "sources": None},
    ]}
    pairs4 = epl._learn_module_pairs(d4, min_pairs=1)
    check("no crash, only sourced entry contributes",
          pairs4.get("errors.log", {}).get("x", {}).get("y") == 1, f"-> {pairs4}")

    print("== 5. predict_module_companions: same-module companion ==")
    d5 = {"history": hist_with_sources([
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
    ])}
    comps = epl.predict_module_companions(d5, {"gateway.log": {"a": 1}})
    check("predicts b", len(comps) == 1 and comps[0]["pattern"] == "b",
          f"-> {comps}")
    check("source attributed to gateway.log", comps and comps[0]["source"] == "gateway.log",
          f"-> {comps and comps[0].get('source')}")
    check("co_score = 3", comps and comps[0]["co_score"] == 3)
    check("message mentions source", comps and "gateway.log" in comps[0]["message"])
    # b сейчас в errors.log, исторической пары там нет -> предсказания нет
    comps_x = epl.predict_module_companions(d5, {"errors.log": {"b": 1}})
    check("no prediction for source without history", comps_x == [], f"-> {comps_x}")

    print("== 6. present excluded, empty inputs -> [] ==")
    d6 = {"history": hist_with_sources([
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
        ({"a": 1, "b": 1}, {"gateway.log": {"a": 1, "b": 1}}),
    ])}
    check("both present -> no companion",
          epl.predict_module_companions(d6, {"gateway.log": {"a": 1, "b": 1}}) == [])
    check("no current sources -> []",
          epl.predict_module_companions(d6, {}) == [])
    check("no history -> []",
          epl.predict_module_companions({"history": []}, {"gateway.log": {"a": 1}}) == [])
    check("empty pats in source -> skip",
          epl.predict_module_companions(d6, {"gateway.log": {}}) == [])

    print("== 7. max_companions limit + ordering ==")
    d7 = {"history": hist_with_sources([
        ({"a": 1, "b": 1, "c": 1, "d": 1, "e": 1},
         {"gateway.log": {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1}}),
        ({"a": 1, "b": 1, "c": 1, "d": 1},
         {"gateway.log": {"a": 1, "b": 1, "c": 1, "d": 1}}),
        ({"a": 1, "b": 1, "c": 1},
         {"gateway.log": {"a": 1, "b": 1, "c": 1}}),
    ])}
    comps7 = epl.predict_module_companions(
        d7, {"gateway.log": {"a": 1}}, max_companions=3)
    check("top-3 by score", [c["pattern"] for c in comps7] == ["b", "c", "d"],
          f"-> {[c['pattern'] for c in comps7]}")
    check("scores desc", [c["co_score"] for c in comps7] == [3, 3, 2],
          f"-> {[c['co_score'] for c in comps7]}")

    print("== 8. update_patterns persists sources/module pairs (integration) ==")
    log = tmp / "gateway.log"
    log.write_text("ERROR connection refused on port 8080\n"
                   "ERROR Gateway Timeout upstream\n")
    r1 = epl.update_patterns()
    r2 = epl.update_patterns()
    log.write_text("ERROR connection refused on port 8080\n")
    r3 = epl.update_patterns()
    saved = json.loads(epl.PATTERNS_FILE.read_text())
    hist_last = saved["history"][-1]
    check("history entry has sources", "sources" in hist_last
          and "gateway.log" in hist_last["sources"], f"-> keys: {list(hist_last.keys())}")
    check("module_cooccurrences persisted",
          "module_cooccurrences" in saved
          and saved["module_cooccurrences"].get("gateway.log", {})
              .get("connection_refused", {}).get("gateway_timeout") == 2,
          f"-> {saved.get('module_cooccurrences', {}).get('gateway.log', {}).get('connection_refused')}")
    check("module_companions key in result", "module_companions" in r3,
          f"-> keys: {list(r3.keys())}")
    check("gateway_timeout predicted in same module (run 3)",
          any(c["pattern"] == "gateway_timeout" and c["source"] == "gateway.log"
              for c in r3["module_companions"]),
          f"-> {r3['module_companions']}")
    check("run 2 had no module companion (both present)",
          all(c["pattern"] != "gateway_timeout" for c in r2["module_companions"]),
          f"-> {r2['module_companions']}")
    check("aggregate companions still work (v4 backward compat)",
          "companions" in r3 and any(c["pattern"] == "gateway_timeout" for c in r3["companions"]),
          f"-> {r3['companions']}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
