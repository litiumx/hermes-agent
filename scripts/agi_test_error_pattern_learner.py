#!/usr/bin/env python3
"""agi_test_error_pattern_learner.py — тесты error_pattern_learner (цикл 07.08).

Покрывает:
1. Импорт без write-доступа к /root/.hermes/data (cron-песочница) — не падает
2. _normalize_line: hex-адреса схлопываются в 0xADDR ДО замены цифр
   (регрессия: раньше 0x[0-9a-f]+ был мёртв — буквенные адреса 0xdeadbeef
   не схлопывались)
3. _normalize_line: цифры→N, уровень+модуль срезаются, check_*→check_FN
4. learn_new_patterns: дедуп по нормализованной строке, повторный вызов = 0 новых
5. _pattern_trend: rising/falling/stable/new
6. predict_risks: falling → low, rising → high
7. scan_logs: считает СТРОКИ с ошибкой по tmp-логам
8. update_patterns: пишет в tmp PATTERNS_FILE, risks/trends персистятся
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


def main():
    # Безопасный импорт уже произошёл наверху (раньше падал PermissionError).
    print("== 1. import safe without writable /root/.hermes/data ==")
    check("module imported", callable(epl.update_patterns))

    print("== 2. normalize hex addresses ==")
    n1 = epl._normalize_line("ERROR 0x7f3a1b2c segfault in worker")
    check("hex with digits -> 0xADDR", "0xADDR" in n1, f"-> {n1!r}")
    n2 = epl._normalize_line("ERROR 0xdeadbeef segfault in worker")
    check("hex letters-only -> 0xADDR (regression)", "0xADDR" in n2, f"-> {n2!r}")
    n3 = epl._normalize_line("WARN 0xABCDEF01 retry attempt")
    check("hex mixed case -> 0xADDR", "0xADDR" in n3 and "0xABCDEF01" not in n3, f"-> {n3!r}")

    print("== 3. normalize digits/prefix/check_* ==")
    n4 = epl._normalize_line("ERROR [gw-42] gateway: check_task_123 failed on port 8080")
    check("digits -> N", "port N" in n4 and "8080" not in n4, f"-> {n4!r}")
    check("level+id stripped", not n4.startswith("ERROR"), f"-> {n4!r}")
    check("check_* -> check_FN", "check_FN" in n4, f"-> {n4!r}")

    print("== 4. learn_new_patterns dedup ==")
    tmp = Path(tempfile.mkdtemp(prefix="epl_test_"))
    epl.LOG_DIR = tmp
    epl.SUPERVISOR_LOG = tmp / "nonexistent.md"
    epl.SESSION_DIR = tmp / "sessions"
    (tmp / "errors.log").write_text(
        "ERROR registry check_task_12 failed\n"
        "ERROR registry check_task_99 failed\n"
        "ERROR registry check_task_7 failed\n"  # та же норм. строка
        "ERROR unrelated rare thing happened\n"
    )
    data = {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}
    new1 = epl.learn_new_patterns(data, min_occurrences=1)
    check("learned 2 patterns", len(new1) == 2, f"-> {[p['name'] for p in new1]}")
    names = {p["name"] for p in new1}
    check("registry noise collapsed to 1", len([p for p in new1 if "check_FN" in p["pattern"]]) == 1,
          f"-> {[p['pattern'] for p in new1]}")
    data["learned_patterns"] = (data.get("learned_patterns", []) + new1)[-epl.MAX_LEARNED:]
    new2 = epl.learn_new_patterns(data, min_occurrences=1)
    check("no re-learn on 2nd pass", len(new2) == 0, f"-> {len(new2)}")

    print("== 5. _pattern_trend ==")
    def hist(pats_list):
        return [{"timestamp": time.time(), "error_count": 1, "patterns": p} for p in pats_list]
    d_rise = {"history": hist([{}, {}, {"x": 1}, {"x": 1}, {"x": 1}, {"x": 1}])}
    check("rising", epl._pattern_trend(d_rise, "x") == "rising",
          f"-> {epl._pattern_trend(d_rise, 'x')}")
    d_fall = {"history": hist([{"x": 1}, {"x": 1}, {"x": 1}, {"x": 0}, {}, {}])}
    check("falling", epl._pattern_trend(d_fall, "x") == "falling",
          f"-> {epl._pattern_trend(d_fall, 'x')}")
    d_stab = {"history": hist([{"x": 1}, {"x": 1}, {"x": 1}, {"x": 1}, {"x": 1}, {"x": 1}])}
    check("stable", epl._pattern_trend(d_stab, "x") == "stable",
          f"-> {epl._pattern_trend(d_stab, 'x')}")
    check("new (absent)", epl._pattern_trend(d_stab, "zzz") == "new",
          f"-> {epl._pattern_trend(d_stab, 'zzz')}")

    print("== 6. predict_risks falling -> low, rising -> high ==")
    d_risks = {
        "history": hist([{"a": 1}, {"a": 1, "b": 1}, {"a": 1, "b": 1}, {"b": 1}, {"b": 1}, {"b": 1}]),
        "streaks": {"a": 4, "b": 5},
    }
    risks = {r["pattern"]: r["risk"] for r in epl.predict_risks(d_risks)}
    check("falling a -> low", risks.get("a") == "low", f"-> {risks}")
    check("rising b -> high", risks.get("b") == "high", f"-> {risks}")

    print("== 7. scan_logs counts lines in tmp logs ==")
    (tmp / "gateway.log").write_text("ok line\nERROR [Errno 2] No such file or directory: /x\n")
    matches = epl.scan_logs(data)
    check("file_not_found detected", matches.get("file_not_found", 0) >= 1, f"-> {matches}")

    print("== 8. update_patterns persists to tmp file ==")
    epl.PATTERNS_FILE = tmp / "error_patterns.json"
    res = epl.update_patterns()
    check("status ok", res["status"] == "ok", f"-> {res['status']}")
    check("learned persisted", epl.PATTERNS_FILE.exists() and epl.PATTERNS_FILE.stat().st_size > 0)
    saved = json.loads(epl.PATTERNS_FILE.read_text())
    check("trends persisted", "trends" in saved, f"-> keys: {list(saved.keys())}")
    check("risks persisted", "risks" in saved)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
