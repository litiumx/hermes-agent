#!/usr/bin/env python3
"""agi_test_session_bridge_history_json.py — JSON-fallback для history CLI (цикл 33).

Grow point цикла 32: JSON-ветка `history` существовала, но не тестировалась
напрямую и не имела паритета с SQLite-веткой (get_session_history фильтрует
по окну часов; JSON перечислял ВСЕ снапшоты без фильтра).

Покрывает history_json(hours=24):
1. Пустой каталог истории -> []
2. Сортировка новые->старые, ключи {timestamp, session_phase, last_task}
3. Фильтр по окну часов (паритет с SQLite get_session_history)
4. Legacy-снапшот БЕЗ timestamp включается (возраст неизвестен — не теряем),
   даже при нулевом окне
5. Битый снапшот (JSONDecodeError) пропускается, остальные возвращаются
6. Отсутствующие поля -> дефолты ("?", "?")
7. Каталога истории нет -> [] (без падения)
8. CLI-формат: ts-метка legacy = "??.?? ??:??" через форматтер CLI
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agi_session_bridge as sb

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bridge_history_json_"))
    sb.SESSION_DIR = tmp
    sb.HISTORY_DIR = tmp / "history"
    sb.BRIDGE_FILE = sb.SESSION_DIR / "bridge.json"
    sb._USE_SQLITE = False  # принудительно JSON-бэкенд (изоляция)
    sb.HISTORY_DIR.mkdir(exist_ok=True)

    print("== 1. пустой каталог истории ==")
    check("пусто -> []", sb.history_json() == [], f"-> {sb.history_json()}")

    print("== 2. сортировка и структура записей ==")
    now = time.time()
    sb._archive_snapshot({"timestamp": now - 99 * 3600, "last_task": "старая",
                          "session_phase": "complete"})
    sb._archive_snapshot({"timestamp": now - 0.5 * 3600, "last_task": "средняя",
                          "session_phase": "interrupted"})
    sb._archive_snapshot({"timestamp": now - 0.01, "last_task": "новая",
                          "session_phase": "error"})
    hist = sb.history_json(hours=200)
    check("3 записи", len(hist) == 3, f"-> {len(hist)}")
    check("новые->старые", [h["last_task"] for h in hist] == ["новая", "средняя", "старая"],
          f"-> {[h['last_task'] for h in hist]}")
    check("ключи записи", set(hist[0]) == {"timestamp", "session_phase", "last_task"},
          f"-> {set(hist[0])}")
    check("session_phase сохранился", hist[1]["session_phase"] == "interrupted")

    print("== 3. фильтр по окну часов (паритет с SQLite) ==")
    hist_24h = sb.history_json(hours=24)
    check("окно 24ч -> 2 свежие", len(hist_24h) == 2 and
          all(h["last_task"] in ("новая", "средняя") for h in hist_24h),
          f"-> {[h['last_task'] for h in hist_24h]}")
    hist_100h = sb.history_json(hours=100)
    check("окно 100ч -> все 3 (99ч на границе)", len(hist_100h) == 3, f"-> {len(hist_100h)}")

    print("== 4. legacy без timestamp включается (возраст неизвестен) ==")
    (sb.HISTORY_DIR / "snapshot_9999999999999.json").write_text(json.dumps({
        "last_task": "легаси без ts", "session_phase": "complete"}))
    hist_legacy = sb.history_json(hours=0)  # окно ноль: всё с ts отсекается
    check("legacy в окне 0ч", any(h["last_task"] == "легаси без ts" for h in hist_legacy),
          f"-> {[h['last_task'] for h in hist_legacy]}")
    check("legacy timestamp=0", all(h["timestamp"] == 0 for h in hist_legacy
                                    if h["last_task"] == "легаси без ts"))

    print("== 5. битый снапшот пропускается ==")
    (sb.HISTORY_DIR / "snapshot_1111111111111.json").write_text("{corrupt json!!!")
    hist_ok = sb.history_json(hours=200)
    check("битый пропущен, остальные целы", len(hist_ok) == 4,
          f"-> {len(hist_ok)}")

    print("== 6. отсутствующие поля -> дефолты ==")
    (sb.HISTORY_DIR / "snapshot_2222222222222.json").write_text(json.dumps({}))
    hist_def = sb.history_json(hours=200)
    check("дефолты '?'", any(h["session_phase"] == "?" and h["last_task"] == "?"
                             for h in hist_def), f"-> {hist_def}")

    print("== 7. каталога истории нет ==")
    old_hist_dir = sb.HISTORY_DIR
    sb.HISTORY_DIR = tmp / "no_such_history_dir"
    check("нет каталога -> []", sb.history_json() == [], f"-> {sb.history_json()}")
    sb.HISTORY_DIR = old_hist_dir

    print("== 8. CLI-формат legacy-метки ==")
    (sb.HISTORY_DIR / "snapshot_3333333333333.json").write_text(json.dumps({
        "last_task": "x", "session_phase": "complete"}))
    fmt = sb._format_history_line(sb.history_json(hours=0)[0])
    check("legacy ts -> '??.?? ??:??'", fmt.startswith("[??.?? ??:??]"),
          f"-> {fmt!r}")
    now2 = time.time()
    (sb.HISTORY_DIR / f"snapshot_{now2 * 1000:.0f}.json").write_text(json.dumps({
        "timestamp": now2, "last_task": "y", "session_phase": "complete"}))
    fmt2 = sb._format_history_line(next(h for h in sb.history_json() if h["timestamp"]))
    check("реальный ts форматируется", "??" not in fmt2, f"-> {fmt2!r}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
