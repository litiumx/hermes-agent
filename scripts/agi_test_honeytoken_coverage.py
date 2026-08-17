#!/usr/bin/env python3
"""agi_test_honeytoken_coverage.py — тесты автопосадки приманок (цикл 39).

Grow point WEEKLY_REVIEW_2026-08-17: «автопосадка honeytoken-приманок при
пустом сторе N дней». Если стор пуст дольше empty_days — приманки сажаются
автоматически, детекция выноса никогда не остаётся без покрытия.

Покрывает:
1. стор в норме (valid >= min) → no-op, empty_since не трогается
2. пустой стор, первый вызов → empty_since=now, посадки НЕТ (ждём N дней)
3. пустой стор, вызов раньше empty_days → посадки НЕТ
4. пустой стор, empty_since старше empty_days → посадка min_tokens, сброс empty_since
5. частичное покрытие (0 < valid < min) → досадка сразу, без ожидания
6. битые записи (нет marker/planted_at) не считаются валидными → досадка
7. planted_total растёт корректно (не теряет историю)
8. идемпотентность: повторный вызов после посадки → no-op
9. empty_days=0 → немедленная посадка при пустом сторе
10. CLI auto-plant через subprocess с AGI_HONEY_FILE
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_honeytoken as ht

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append(name)
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))


def make_store(tmp: Path, tokens=None, planted_total=None, empty_since=None) -> Path:
    p = tmp / "honeytokens.json"
    data = {"honeytokens": tokens or []}
    if planted_total is not None:
        data["planted_total"] = planted_total
    if empty_since is not None:
        data["empty_since"] = empty_since
    p.write_text(json.dumps(data))
    return p


def main():
    tmp = Path(tempfile.mkdtemp(prefix="agi_honey_cov_"))
    try:
        now = time.time()
        DAY = 86400

        print("== 1. стор в норме: no-op ==")
        store = make_store(tmp, tokens=[
            {"marker": "AGI_HONEY_aaaaaaaa", "planted_at": now - DAY,
             "note": "honeytoken AGI_HONEY_aaaaaaaa: decoy"},
            {"marker": "AGI_HONEY_bbbbbbbb", "planted_at": now - DAY,
             "note": "honeytoken AGI_HONEY_bbbbbbbb: decoy"},
            {"marker": "AGI_HONEY_cccccccc", "planted_at": now - DAY,
             "note": "honeytoken AGI_HONEY_cccccccc: decoy"},
        ], planted_total=3)
        res = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store)
        check("no plant", res["planted"] == 0, f"-> {res['planted']}")
        check("reason ok", res["reason"] == "ok", f"-> {res['reason']}")
        data = json.loads(store.read_text())
        check("store untouched", len(data["honeytokens"]) == 3)

        print("== 2. пустой стор, первый вызов: empty_since, без посадки ==")
        store2 = make_store(tmp, tokens=[], planted_total=0)
        res2 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store2)
        check("no plant yet", res2["planted"] == 0, f"-> {res2['planted']}")
        check("reason waiting", res2["reason"] == "waiting", f"-> {res2['reason']}")
        data2 = json.loads(store2.read_text())
        check("empty_since set", isinstance(data2.get("empty_since"), (int, float)),
              f"-> {data2.get('empty_since')}")
        check("still empty", len(data2["honeytokens"]) == 0)

        print("== 3. пустой стор, раньше empty_days: посадки нет ==")
        store3 = make_store(tmp, tokens=[], planted_total=0,
                            empty_since=now - 3 * DAY)
        res3 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store3)
        check("no plant before N days", res3["planted"] == 0, f"-> {res3['planted']}")
        check("reason waiting", res3["reason"] == "waiting", f"-> {res3['reason']}")
        data3 = json.loads(store3.read_text())
        check("empty_since kept", data3.get("empty_since") == now - 3 * DAY)

        print("== 4. пустой стор, empty_since старше empty_days: посадка ==")
        store4 = make_store(tmp, tokens=[], planted_total=0,
                            empty_since=now - 10 * DAY)
        res4 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store4)
        check("planted 3", res4["planted"] == 3, f"-> {res4['planted']}")
        check("reason planted", res4["reason"] == "planted", f"-> {res4['reason']}")
        data4 = json.loads(store4.read_text())
        check("now has 3", len(data4["honeytokens"]) == 3, f"-> {len(data4['honeytokens'])}")
        check("markers valid", all(t.get("marker", "").startswith("AGI_HONEY_")
                                   for t in data4["honeytokens"]))
        check("empty_since cleared", data4.get("empty_since") is None,
              f"-> {data4.get('empty_since')}")
        check("planted_total=3", data4.get("planted_total") == 3,
              f"-> {data4.get('planted_total')}")

        print("== 5. частичное покрытие: досадка сразу ==")
        store5 = make_store(tmp, tokens=[
            {"marker": "AGI_HONEY_dddddddd", "planted_at": now - DAY,
             "note": "honeytoken AGI_HONEY_dddddddd: decoy"},
        ], planted_total=1, empty_since=now - 5 * DAY)
        res5 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store5)
        check("topped up 2", res5["planted"] == 2, f"-> {res5['planted']}")
        data5 = json.loads(store5.read_text())
        check("total 3", len(data5["honeytokens"]) == 3, f"-> {len(data5['honeytokens'])}")
        check("empty_since cleared on topup", data5.get("empty_since") is None,
              f"-> {data5.get('empty_since')}")

        print("== 6. битые записи не валидны: досадка ==")
        store6 = make_store(tmp, tokens=[
            {"planted_at": now - DAY},          # нет marker
            {"marker": "AGI_HONEY_eeeeeeee"},    # нет planted_at
        ], planted_total=2, empty_since=now - 10 * DAY)
        res6 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store6)
        check("planted 3 (broken ignored)", res6["planted"] == 3,
              f"-> {res6['planted']}")
        data6 = json.loads(store6.read_text())
        valid6 = [t for t in data6["honeytokens"]
                  if t.get("marker") and t.get("planted_at")]
        check("3 valid now", len(valid6) == 3, f"-> {len(valid6)}")

        print("== 7. planted_total растёт, история не теряется ==")
        check("planted_total=5", data6.get("planted_total") == 5,
              f"-> {data6.get('planted_total')}")

        print("== 8. идемпотентность: после посадки no-op ==")
        res8 = ht.ensure_coverage(min_tokens=3, empty_days=7, store_path=store6)
        check("no second plant", res8["planted"] == 0, f"-> {res8['planted']}")
        check("reason ok", res8["reason"] == "ok")

        print("== 9. empty_days=0: немедленная посадка при пустом сторе ==")
        store9 = make_store(tmp, tokens=[], planted_total=0)
        res9 = ht.ensure_coverage(min_tokens=2, empty_days=0, store_path=store9)
        check("planted 2", res9["planted"] == 2, f"-> {res9['planted']}")

        print("== 10. CLI auto-plant ==")
        cli_store = tmp / "cli_honey.json"
        env = dict(os.environ, AGI_HONEY_FILE=str(cli_store))
        r = subprocess.run(
            [sys.executable, "agi_honeytoken.py", "auto-plant", "--min", "2", "--days", "0"],
            capture_output=True, text=True, env=env, cwd=str(Path(__file__).parent))
        check("CLI exit 0", r.returncode == 0, f"-> {r.returncode}: {r.stderr[:200]}")
        check("CLI planted 2", "planted 2" in r.stdout, f"-> {r.stdout.strip()[:120]}")
        cli_data = json.loads(cli_store.read_text())
        check("CLI store has 2", len(cli_data["honeytokens"]) == 2,
              f"-> {len(cli_data['honeytokens'])}")

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
