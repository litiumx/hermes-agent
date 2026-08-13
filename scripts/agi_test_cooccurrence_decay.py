#!/usr/bin/env python3
"""agi_test_cooccurrence_decay.py — decay-веса пар со-встречаемостей (v7).

Grow point 20: «cooccurrences тоже с decay: пары со-встречаемостей взвешивать
по recency». Раньше каждая пара (a,b) в скане давала ровно +1: скан месячной
давности весил как вчерашний, и устаревшие пары держались в cooccurrences
вечно, плодя старые companion-предсказания. Теперь вес скана
0.5^(age/half_life) как в _decay_scores (v6): свежий ≈1.0, на half-life 0.5,
дальше экспоненциально меньше. decay=False сохраняет старое поведение
(сырые счётчики).
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_error_pattern_learner as epl

FAILURES = []
DAY = 86400
HL = epl.STREAK_HALF_LIFE_DAYS  # 14


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def hist_at(ts_list, pats):
    """История сканов: timestamp из ts_list (каждый элемент — age в секундах
    от now, None = без timestamp), patterns из pats."""
    now = time.time()
    out = []
    for age, p in zip(ts_list, pats):
        h = {"error_count": sum(p.values()) if p else 0, "patterns": p}
        if age is not None:
            h["timestamp"] = now - age
        out.append(h)
    return out


def main():
    print("== 1. fresh scans keep integer weights (backward compat) ==")
    d = {"history": hist_at([0, 0, 0], [{"a": 1, "b": 2}, {"a": 1, "b": 1}, {"a": 1, "b": 3}])}
    co = epl._learn_cooccurrences(d, min_pairs=2)
    check("pair (a,b) = 3.0 from 3 fresh scans",
          co.get("a", {}).get("b") == 3.0, f"-> {co.get('a')}")
    check("symmetric (b,a) = 3.0", co.get("b", {}).get("a") == 3.0)

    print("== 2. half-life: scan 14d ago weighs ~0.5 ==")
    now = time.time()
    check("_scan_weight fresh = 1.0",
          epl._scan_weight({"timestamp": now}, now) == 1.0)
    check("_scan_weight at half-life = 0.5",
          epl._scan_weight({"timestamp": now - HL * DAY}, now) == 0.5)
    check("_scan_weight at 2*half-life = 0.25",
          epl._scan_weight({"timestamp": now - 2 * HL * DAY}, now) == 0.25)
    check("_scan_weight no timestamp = 1.0",
          epl._scan_weight({}, now) == 1.0)
    check("_scan_weight future timestamp = 1.0",
          epl._scan_weight({"timestamp": now + 99999}, now) == 1.0)
    d2 = {"history": hist_at([HL * DAY], [{"a": 1, "b": 1}])}
    co2 = epl._learn_cooccurrences(d2, min_pairs=0.5)
    w = co2.get("a", {}).get("b")
    check("pair weight == 0.5 at half-life", w == 0.5, f"-> {w}")

    print("== 3. old pair decays out at min_pairs=1, fresh pair stays ==")
    # пара X: два скана 28 дней назад (0.25+0.25=0.5 < 1) -> не значима;
    # пара Y: один свежий скан (1.0) -> значима. Одинаковое число
    # совместных появлений (2), разная свежесть.
    d3 = {"history": hist_at(
        [28 * DAY, 28 * DAY, 0],
        [{"x1": 1, "x2": 1}, {"x1": 1, "x2": 1}, {"y1": 1, "y2": 1}])}
    co3 = epl._learn_cooccurrences(d3, min_pairs=1)
    check("old pair dropped (0.5 < 1)", co3.get("x1") is None,
          f"-> {co3.get('x1')}")
    check("fresh pair kept (1.0 >= 1)", co3.get("y1", {}).get("y2") == 1.0,
          f"-> {co3.get('y1')}")

    print("== 4. missing timestamp -> weight 1.0 (conservative fresh) ==")
    d4 = {"history": hist_at([None], [{"a": 1, "b": 1}])}
    co4 = epl._learn_cooccurrences(d4, min_pairs=1)
    check("no-timestamp scan weighs 1.0", co4.get("a", {}).get("b") == 1.0,
          f"-> {co4}")

    print("== 5. broken/future timestamp -> weight 1.0 ==")
    d5 = {"history": [{"timestamp": time.time() + 99999, "patterns": {"a": 1, "b": 1}}]}
    co5 = epl._learn_cooccurrences(d5, min_pairs=1)
    check("future timestamp weighs 1.0", co5.get("a", {}).get("b") == 1.0,
          f"-> {co5}")

    print("== 6. min_pairs filters decayed pairs ==")
    old_pair = {"history": hist_at(
        [28 * DAY, 28 * DAY], [{"a": 1, "b": 1}, {"a": 1, "b": 1}])}
    co6 = epl._learn_cooccurrences(old_pair, min_pairs=2)
    check("two 28d scans (0.5) dropped at min_pairs=2", co6 == {}, f"-> {co6}")
    co6b = epl._learn_cooccurrences(old_pair, min_pairs=0.5)
    check("kept at min_pairs=0.5 with 0.5", co6b.get("a", {}).get("b") == 0.5, f"-> {co6b}")
    fresh_pair = {"history": hist_at(
        [0, 0], [{"a": 1, "b": 1}, {"a": 1, "b": 1}])}
    co6c = epl._learn_cooccurrences(fresh_pair, min_pairs=2)
    check("two fresh scans (2.0) kept at min_pairs=2",
          co6c.get("a", {}).get("b") == 2.0, f"-> {co6c}")

    print("== 7. decay=False keeps raw integer counts ==")
    co7 = epl._learn_cooccurrences(old_pair, min_pairs=1, decay=False)
    check("raw count 2 (not 0.5)", co7.get("a", {}).get("b") == 2, f"-> {co7}")
    check("raw count is int", isinstance(co7["a"]["b"], int), f"-> {type(co7['a']['b'])}")

    print("== 8. symmetry preserved with decay ==")
    d8 = {"history": hist_at([0, HL * DAY], [{"a": 1, "b": 1}, {"a": 1, "b": 1}])}
    co8 = epl._learn_cooccurrences(d8, min_pairs=1)
    check("(a,b) == (b,a) = 1.5", co8["a"]["b"] == co8["b"]["a"] == 1.5,
          f"-> {co8}")

    print("== 9. module pairs decay (per source) ==")
    d9 = {"history": [
        {"timestamp": time.time() - 28 * DAY, "sources": {"gw.log": {"a": 1, "b": 1}}},
        {"timestamp": time.time(), "sources": {"gw.log": {"a": 1, "b": 1}}},
    ]}
    m9 = epl._learn_module_pairs(d9, min_pairs=1)
    check("module pair decayed to 1.25", m9.get("gw.log", {}).get("a", {}).get("b") == 1.25,
          f"-> {m9}")
    m9b = epl._learn_module_pairs(d9, min_pairs=1, decay=False)
    check("module pair raw = 2", m9b.get("gw.log", {}).get("a", {}).get("b") == 2,
          f"-> {m9b}")

    print("== 10. predict_companions uses decayed map (fresh wins) ==")
    d10 = {"history": hist_at(
        [28 * DAY, 28 * DAY, 0, 0],
        [{"a": 1, "o1": 1}, {"a": 1, "o1": 1}, {"a": 1, "f1": 1}, {"a": 1, "f1": 1}])}
    comps = epl.predict_companions(d10, {"a": 1}, min_pairs=0.5)
    check("fresh companion first", comps and comps[0]["pattern"] == "f1",
          f"-> {comps}")
    check("co_score fresh = 2.0", comps and comps[0]["co_score"] == 2.0,
          f"-> {comps and comps[0]['co_score']}")
    check("old companion present with 0.5",
          any(c["pattern"] == "o1" and c["co_score"] == 0.5 for c in comps),
          f"-> {comps}")
    check("message mentions weight", comps and "вес" in comps[0]["message"],
          f"-> {comps and comps[0]['message']}")

    print("== 11. legacy entries without patterns skipped (no crash) ==")
    d11 = {"history": [
        {"timestamp": time.time() - 30 * DAY, "error_count": 2},  # no patterns
        {"timestamp": time.time() - 30 * DAY, "patterns": None},   # corrupted
        {"timestamp": time.time(), "patterns": {"a": 1, "b": 1}},
    ]}
    co11 = epl._learn_cooccurrences(d11, min_pairs=1)
    check("no crash, fresh pair = 1.0", co11.get("a", {}).get("b") == 1.0,
          f"-> {co11}")

    print("== 12. update_patterns persists decayed cooccurrences (integration) ==")
    tmp = Path(tempfile.mkdtemp(prefix="epl_decay_"))
    epl.LOG_DIR = tmp
    epl.SUPERVISOR_LOG = tmp / "nonexistent.md"
    epl.SESSION_DIR = tmp / "sessions"
    epl.PATTERNS_FILE = tmp / "error_patterns.json"
    log = tmp / "errors.log"
    log.write_text("ERROR connection refused on port 8080\nERROR Gateway Timeout upstream\n")
    epl.update_patterns()
    epl.update_patterns()
    saved = json.loads(epl.PATTERNS_FILE.read_text())
    v = saved["cooccurrences"].get("connection_refused", {}).get("gateway_timeout")
    check("persisted pair == 2.0 (two fresh scans)",
          v == 2.0, f"-> {saved.get('cooccurrences', {}).get('connection_refused')}")
    vmod = saved["module_cooccurrences"].get("errors.log", {}).get("connection_refused", {}).get("gateway_timeout")
    check("module pair persisted == 2.0", vmod == 2.0, f"-> {vmod}")

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("RESULT: ALL TESTS PASS")


if __name__ == "__main__":
    main()
