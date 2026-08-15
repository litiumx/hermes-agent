#!/usr/bin/env python3
"""agi_test_honeytoken.py — тесты honeytoken-детектора выноса памяти (цикл 31).

Покрывает:
1. plant: создаёт N приманок, уникальные маркеры AGI_HONEY_<8hex>, персист в JSON
2. plant повторно: новые маркеры, старые не дублируются, общий счёт растёт
3. note приманки содержит маркер (полный вынос записи детектится)
4. check_exfil: находит маркер в тексте, несколько маркеров — все
5. check_exfil: чистый текст / пустой текст / None — []
6. битый JSON стора: check — [], plant — чинит
7. verify: все на месте; после удаления записи — детектит пропажу
8. status: счётчики, возраст
9. CLI: plant/check/verify через subprocess, exit 1 на утечку, 0 на чисто
10. check по пути файла (аргумент-файл читается)
"""
import json
import os
import re
import shutil
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


MARKER_RE = re.compile(r"^AGI_HONEY_[0-9a-f]{8}$")


def fresh_store(tmp: Path) -> Path:
    p = tmp / "honeytokens.json"
    return p


def main():
    tmp = Path(tempfile.mkdtemp(prefix="agi_honey_"))
    try:
        print("== 1. plant: N приманок, уникальные маркеры, персист ==")
        store = fresh_store(tmp)
        tokens = ht.plant(3, store_path=store)
        check("planted 3", len(tokens) == 3, f"-> {len(tokens)}")
        markers = [t["marker"] for t in tokens]
        check("markers unique", len(set(markers)) == 3, f"-> {markers}")
        check("marker format", all(MARKER_RE.match(m) for m in markers), f"-> {markers}")
        check("store file exists", store.exists())
        data = json.loads(store.read_text())
        check("persisted 3", len(data.get("honeytokens", [])) == 3,
              f"-> {len(data.get('honeytokens', []))}")

        print("== 2. plant повторно: новые маркеры, старые целы ==")
        tokens2 = ht.plant(2, store_path=store)
        data = json.loads(store.read_text())
        all_markers = [t["marker"] for t in data["honeytokens"]]
        check("total 5", len(all_markers) == 5, f"-> {len(all_markers)}")
        check("old markers kept", set(markers) <= set(all_markers))
        check("new markers unique", len(set(all_markers)) == 5)

        print("== 3. note содержит маркер (вынос всей записи детектится) ==")
        tok = data["honeytokens"][0]
        check("note has marker", tok["marker"] in tok.get("note", ""),
              f"-> note: {tok.get('note', '')[:60]}")

        print("== 4. check_exfil: находит маркеры ==")
        leaked = ht.check_exfil(f"payload with {tok['marker']} inside", store_path=store)
        check("found 1", len(leaked) == 1, f"-> {leaked}")
        check("found right marker", leaked[0]["marker"] == tok["marker"])
        m2, m3 = all_markers[1], all_markers[2]
        leaked2 = ht.check_exfil(f"{m2} and {m3} both", store_path=store)
        check("found 2", len(leaked2) == 2, f"-> {[l['marker'] for l in leaked2]}")

        print("== 5. check_exfil: чистый текст ==")
        check("clean text", ht.check_exfil("no secrets here", store_path=store) == [])
        check("empty text", ht.check_exfil("", store_path=store) == [])
        check("None text", ht.check_exfil(None, store_path=store) == [])
        check("marker-like but unknown", ht.check_exfil("AGI_HONEY_ffffffff", store_path=store) == [])

        print("== 6. битый JSON стора ==")
        store.write_text("{not json!!")
        check("check on broken store", ht.check_exfil("x", store_path=store) == [])
        toks = ht.plant(1, store_path=store)
        check("plant fixes store", len(toks) == 1 and store.exists())

        print("== 7. verify: целостность ==")
        ok = ht.verify(store_path=store)
        check("all present", ok["missing"] == [] and ok["removed"] == 0, f"-> {ok}")
        data = json.loads(store.read_text())
        data["honeytokens"] = data["honeytokens"][:-1]
        store.write_text(json.dumps(data))
        ok2 = ht.verify(store_path=store)
        check("removed detected", ok2["removed"] == 1, f"-> removed={ok2['removed']}")
        # битая запись (нет marker) → missing
        data = json.loads(store.read_text())
        data["honeytokens"].append({"planted_at": time.time(), "note": "broken"})
        store.write_text(json.dumps(data))
        ok3 = ht.verify(store_path=store)
        check("corrupt record in missing", len(ok3["missing"]) == 1, f"-> {ok3['missing']}")
        check("removed still counted", ok3["removed"] == 1, f"-> removed={ok3['removed']}")

        print("== 8. status ==")
        ht.plant(2, store_path=store)  # независимое состояние для status
        st = ht.status(store_path=store)
        check("status count", st["total"] == 2, f"-> {st}")
        check("status has age", "oldest_age_h" in st)

        print("== 9. CLI: plant/check/verify ==")
        cli_store = tmp / "cli_honeytokens.json"  # отдельный файл от тестов 1-8
        env = dict(os.environ)
        env["AGI_HONEY_FILE"] = str(cli_store)
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "agi_honeytoken.py"),
                            "plant", "2"], capture_output=True, text=True, env=env, timeout=60)
        check("cli plant exit 0", r.returncode == 0, f"-> {r.returncode}: {r.stderr[-200:]}")
        m = json.loads(cli_store.read_text())["honeytokens"][0]["marker"]
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "agi_honeytoken.py"),
                            "check", f"leaked {m}!"], capture_output=True, text=True, env=env, timeout=60)
        check("cli check leak exit 1", r.returncode == 1, f"-> {r.returncode}: {r.stdout[-200:]}")
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "agi_honeytoken.py"),
                            "check", "clean text"], capture_output=True, text=True, env=env, timeout=60)
        check("cli check clean exit 0", r.returncode == 0, f"-> {r.returncode}")
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "agi_honeytoken.py"),
                            "verify"], capture_output=True, text=True, env=env, timeout=60)
        check("cli verify exit 0", r.returncode == 0, f"-> {r.returncode}: {r.stderr[-200:]}")
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "agi_honeytoken.py"),
                            "unknown-cmd"], capture_output=True, text=True, env=env, timeout=60)
        check("cli unknown cmd exit 2", r.returncode == 2, f"-> {r.returncode}")

        print("== 10. check по пути файла ==")
        store_marker = next(t["marker"] for t in json.loads(store.read_text())["honeytokens"]
                            if t.get("marker"))
        leak_file = tmp / "leak.txt"
        leak_file.write_text(f"session export with {store_marker}")
        leaked3 = ht.check_exfil(str(leak_file), store_path=store)
        check("file path scanned", len(leaked3) == 1, f"-> {leaked3}")
        check("file found right marker", leaked3[0]["marker"] == store_marker)

        print()
        if FAILURES:
            print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
            sys.exit(1)
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
