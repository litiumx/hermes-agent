#!/usr/bin/env python3
"""Тесты agi_config_guard.py — цикл 06.08 (после улучшений).

Покрытие:
  1. валидные json+yaml → все "ok", exit 0
  2. битый json → corrupt с line/col, exit 1
  3. пустой json → "empty" (не corrupt, не ok) — placeholder не шумит
  4. битый patterns.json → _write_to_patterns возвращает False, файл не тронут
  5. атомарная запись: tmp-файл не остаётся, patterns.json валиден после --write
  6. --strict без PyYAML и с yaml-файлами → exit 2
  7. --strict когда YAML проверен → exit 0 (не ломает нормальный путь)
  8. --json: поля checked/corrupt/empty/skipped/ok, exit 0 при чистоте
  9. main(argv=[]) без sys.argv — регрессия тестируемости

Запуск: python3 agi_test_config_guard.py  (нужен PyYAML для кейсов 1/7)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_config_guard as agc

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def make_env():
    """Временный ROOT: config.yaml + data/error_patterns.json + session/."""
    tmp = Path(tempfile.mkdtemp(prefix="cfg_guard_test_"))
    (tmp / "data").mkdir()
    (tmp / "session").mkdir()
    (tmp / "config.yaml").write_text("gateway:\n  port: 8080\n")
    agc.ROOT = tmp
    agc.PATTERNS_FILE = tmp / "data" / "error_patterns.json"
    agc.SCAN_DIRS = [tmp, tmp / "data", tmp / "session"]  # пересчитать: const при импорте
    agc.PATTERNS_FILE.write_text(json.dumps({"old": 1}))
    return tmp


def main():
    print("agi_test_config_guard.py")

    # --- 1. валидные файлы → ok, exit 0 ---
    tmp = make_env()
    (tmp / "data" / "good.json").write_text('{"a": 1}')
    (tmp / "session" / "s.json").write_text("[]")
    r = agc.validate()
    # YAML может быть "ok" (PyYAML есть) или "skip:PyYAML" (нет) — оба валидны
    ok_or_skip = all(v == "ok" or v == "skip:PyYAML" for v in r.values())
    check("1. валидные json+yaml → ok/skip, без corrupt", ok_or_skip, str(r))
    check("1b. exit 0", agc.main(["--json"]) == 0)

    # --- 2. битый json → corrupt + line/col ---
    (tmp / "data" / "bad.json").write_text('{"a": }')
    r = agc.validate()
    bad = [v for v in r.values() if v.startswith("corrupt")]
    check("2. битый json → corrupt", len(bad) == 1 and "line" in bad[0], str(bad))
    check("2b. exit 1", agc.main(["--json"]) == 1)

    # --- 3. пустой json → empty, не corrupt ---
    (tmp / "data" / "empty.json").write_text("")
    r = agc.validate()
    st = r.get(str(tmp / "data" / "empty.json"))
    check("3. пустой json → empty", st == "empty", str(st))
    check("3b. empty не влияет на exit", agc.main(["--json"]) == 1)  # всё ещё 1 из-за bad.json

    # --- 4. битый patterns.json → False, не тронут ---
    agc.PATTERNS_FILE.write_text("{broken")
    before = agc.PATTERNS_FILE.read_text()
    check("4. битый patterns.json → False", agc._write_to_patterns(0) is False)
    check("4b. файл не изменён", agc.PATTERNS_FILE.read_text() == before)

    # --- 5. атомарная запись, tmp не остаётся ---
    (tmp / "data" / "bad.json").unlink()          # убрать corrupt → clean env
    (tmp / "data" / "empty.json").unlink()
    agc.PATTERNS_FILE.write_text(json.dumps({"old": 1}))
    check("5. --write → True", agc._write_to_patterns(0) is True)
    check("5b. tmp не остался", not (tmp / "data" / "error_patterns.json.tmp").exists())
    fresh = json.loads(agc.PATTERNS_FILE.read_text())
    check("5c. patterns валиден + ключ записан",
          fresh.get("config_corrupt_real") == 0 and fresh.get("old") == 1, str(fresh))

    # --- 6. --strict без PyYAML → exit 2 ---
    real_yaml = agc.yaml
    agc.yaml = None  # симулируем отсутствие PyYAML
    rc = agc.main(["--strict"])
    agc.yaml = real_yaml
    check("6. --strict без PyYAML → exit 2", rc == 2, f"rc={rc}")

    # --- 7. --strict с PyYAML → exit 0 ---
    if real_yaml is not None:
        check("7. --strict с PyYAML → exit 0", agc.main(["--strict"]) == 0)
    else:
        print("  ⚠️ 7. PyYAML не установлен — кейс пропущен (нужен pip install pyyaml)")

    # --- 8. --json структура ---
    (tmp / "data" / "bad2.json").write_text("{x")
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = agc.main(["--json"])
    out = json.loads(buf.getvalue())
    check("8. --json поля", set(out) >= {"checked", "corrupt", "empty", "skipped", "ok"}, str(out))
    check("8b. --json exit 1 при corrupt", rc == 1)

    # --- 9. main(argv=[]) не падает ---
    (tmp / "data" / "bad2.json").unlink()
    check("9. main([]) чистый exit 0", agc.main([]) == 0)

    print(f"\nРезультат: {PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
