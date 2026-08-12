#!/usr/bin/env python3
"""agi_test_error_pattern_decay.py — тесты v6: временной decay паттернов (цикл 19).

Покрывает:
1. _decay_scores: пустая история → {}
2. Один свежий скан → вес ≈ 1.0
3. Один скан на возрасте half-life → вес ≈ 0.5
4. Свежий + старый (2×half-life) → ≈ 1.25
5. Присутствие, а не объём: count=3 в скане == count=1 (вес тот же)
6. Паттерн не в скане → не учитывается
7. Запись без timestamp трактуется как свежая (age 0)
8. _decay_scores: мульти-паттерн карта
9. predict_risks v6: СТАРЫЙ streak (3 появления 60 дней назад) → low
   (раньше rising-trend дал бы high — старые streaks больше не весят)
10. predict_risks v6: свежий streak (3 свежих скана) → high
11. predict_risks: falling + свежий → low (старая семантика сохранена)
12. Каждый risk содержит decay_score
13. update_patterns персистит decay_scores в patterns.json
14. Записи без "patterns" (legacy) не роняют _decay_scores
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


def fresh_scan(patterns, ts=None):
    return {"timestamp": ts if ts is not None else time.time(),
            "error_count": sum(patterns.values()), "patterns": patterns}


def main():
    print("== 1. _decay_scores empty history ==")
    check("empty -> {}", epl._decay_scores({"history": []}) == {}, f"-> {epl._decay_scores({'history': []})}")

    print("== 2. single fresh scan -> ~1.0 ==")
    d = {"history": [fresh_scan({"a": 1})]}
    s = epl._decay_scores(d)
    check("a ~ 1.0", abs(s.get("a", 0) - 1.0) < 0.05, f"-> {s}")

    print("== 3. single scan at half-life age -> ~0.5 ==")
    old = time.time() - epl.STREAK_HALF_LIFE_DAYS * 86400
    d = {"history": [fresh_scan({"a": 1}, ts=old)]}
    s = epl._decay_scores(d)
    check("a ~ 0.5", abs(s.get("a", 0) - 0.5) < 0.05, f"-> {s}")

    print("== 4. fresh + 2x half-life -> ~1.25 ==")
    very_old = time.time() - 2 * epl.STREAK_HALF_LIFE_DAYS * 86400
    d = {"history": [fresh_scan({"a": 1}, ts=very_old), fresh_scan({"a": 1})]}
    s = epl._decay_scores(d)
    check("a ~ 1.25", abs(s.get("a", 0) - 1.25) < 0.07, f"-> {s}")

    print("== 5. presence not volume: count=3 == count=1 ==")
    d3 = {"history": [fresh_scan({"a": 3})]}
    s3 = epl._decay_scores(d3)
    check("count=3 -> ~1.0 (presence)", abs(s3.get("a", 0) - 1.0) < 0.05, f"-> {s3}")

    print("== 6. pattern absent from scan not counted ==")
    d = {"history": [fresh_scan({"b": 1}), fresh_scan({"c": 2})]}
    s = epl._decay_scores(d)
    check("a absent -> 0", s.get("a", 0) == 0, f"-> {s}")

    print("== 7. missing timestamp treated as fresh ==")
    d = {"history": [{"patterns": {"a": 1}}]}  # без timestamp
    s = epl._decay_scores(d)
    check("a ~ 1.0", abs(s.get("a", 0) - 1.0) < 0.05, f"-> {s}")

    print("== 8. multi-pattern map ==")
    d = {"history": [fresh_scan({"a": 1}), fresh_scan({"a": 1, "b": 1})]}
    s = epl._decay_scores(d)
    check("keys {a,b}", set(s.keys()) == {"a", "b"}, f"-> {set(s.keys())}")
    check("b == 1.0", abs(s.get("b", 0) - 1.0) < 0.05, f"-> {s}")

    print("== 9. predict_risks: OLD streak -> low (v6 core) ==")
    old = time.time() - 60 * 86400  # 60 дней назад
    d_old = {
        "history": [fresh_scan({"oldp": 1}, ts=old)] * 3,
        "streaks": {"oldp": 3},
    }
    risks = epl.predict_risks(d_old)
    r_old = next((r for r in risks if r.get("pattern") == "oldp"), None)
    check("old streak present in risks", r_old is not None, f"-> {risks}")
    if r_old:
        check("old streak -> low", r_old["risk"] == "low", f"-> {r_old['risk']}")
        check("decay_score < floor", r_old.get("decay_score", 999) < epl.RISK_DECAY_FLOOR,
              f"-> {r_old.get('decay_score')}")

    print("== 10. predict_risks: FRESH streak -> high ==")
    d_fresh = {
        "history": [fresh_scan({"freshp": 1})] * 3,
        "streaks": {"freshp": 3},
    }
    risks = epl.predict_risks(d_fresh)
    r_fresh = next((r for r in risks if r.get("pattern") == "freshp"), None)
    check("fresh streak present", r_fresh is not None, f"-> {risks}")
    if r_fresh:
        check("fresh streak -> high", r_fresh["risk"] == "high", f"-> {r_fresh['risk']}")
        check("decay_score >= floor", r_fresh.get("decay_score", 0) >= epl.RISK_DECAY_FLOOR,
              f"-> {r_fresh.get('decay_score')}")

    print("== 11. predict_risks: falling + fresh -> low (old semantics kept) ==")
    d_fall = {
        "history": [fresh_scan({"f": 1}), fresh_scan({"f": 1}), fresh_scan({"f": 1}),
                    fresh_scan({}), fresh_scan({}), fresh_scan({})],
        "streaks": {"f": 3},
    }
    risks = epl.predict_risks(d_fall)
    r_f = next((r for r in risks if r.get("pattern") == "f"), None)
    check("falling fresh -> low", r_f is not None and r_f["risk"] == "low",
          f"-> {risks}")

    print("== 12. every risk carries decay_score ==")
    check("decay_score field", r_fresh is not None and "decay_score" in r_fresh,
          f"-> {r_fresh}")
    check("decay_score float", r_fresh is not None and isinstance(r_fresh["decay_score"], float),
          f"-> {type(r_fresh.get('decay_score')) if r_fresh else None}")

    print("== 13. update_patterns persists decay_scores ==")
    tmp = Path(tempfile.mkdtemp(prefix="epl_decay_"))
    epl.LOG_DIR = tmp
    epl.SUPERVISOR_LOG = tmp / "nonexistent.md"
    epl.SESSION_DIR = tmp / "sessions"
    epl.PATTERNS_FILE = tmp / "error_patterns.json"
    seed = {"history": [fresh_scan({"x": 1})], "streaks": {}, "learned_patterns": [],
            "last_update": 0}
    epl.PATTERNS_FILE.write_text(json.dumps(seed))
    res = epl.update_patterns()
    check("decay_scores in result", "decay_scores" in res, f"-> keys: {list(res.keys())}")
    saved = json.loads(epl.PATTERNS_FILE.read_text())
    check("decay_scores persisted", "decay_scores" in saved)
    check("persisted x ~ 1.0", abs(saved.get("decay_scores", {}).get("x", 0) - 1.0) < 0.05,
          f"-> {saved.get('decay_scores')}")

    print("== 14. legacy entries without patterns -> no crash ==")
    d_legacy = {"history": [{"timestamp": time.time()}, {"error_count": 1}]}
    s = epl._decay_scores(d_legacy)
    check("no crash, empty", s == {}, f"-> {s}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
