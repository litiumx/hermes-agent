#!/usr/bin/env python3
"""agi_test_context_store_tiers.py — тесты Saucedo Multi-Tier Memory (цикл 22).

Покрытие: store/get с тирами, валидация входов, upsert, access_count,
consolidate по возрасту и частоте (short→medium→long), promote только вверх,
пустая БД, статистика, list с фильтром, CLI-интеграция (subprocess),
отчёт get_report содержит секцию памяти.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_context_store as cs

TMP = tempfile.mkdtemp(prefix="agi_tiers_test_")
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


def _backdate(key, seconds_ago):
    """Сдвинуть updated_at записи памяти в прошлое."""
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE memory_items SET updated_at = ? WHERE key = ?",
            (time.time() - seconds_ago, key),
        )
        conn.commit()


def _set_access(key, n):
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE memory_items SET access_count = ? WHERE key = ?", (n, key)
        )
        conn.commit()


def test_store_get_basic():
    print("store/get базовый")
    added = cs.store_memory("db_host", "10.0.0.5")
    check("новая запись → True", added is True)
    m = cs.get_memory("db_host")
    check("get возвращает dict", isinstance(m, dict))
    check("value сохранён", m.get("value") == "10.0.0.5")
    check("tier по умолчанию short", m.get("tier") == "short")
    check("access_count=1 после get", m.get("access_count") == 1)


def test_store_validation():
    print("валидация входов")
    check("пустой ключ → False", cs.store_memory("", "x") is False)
    check("не-str ключ → False", cs.store_memory(42, "x") is False)
    check("невалидный tier → False", cs.store_memory("k", "v", tier="ultra") is False)
    check("валидный tier medium → True", cs.store_memory("k_med", "v", tier="medium") is True)
    m = cs.get_memory("k_med")
    check("tier medium сохранён", m.get("tier") == "medium")


def test_store_upsert():
    print("upsert: повторный store того же ключа")
    cs.store_memory("upsert_key", "old")
    added = cs.store_memory("upsert_key", "new", tier="long")
    check("повторный store → False (не новый)", added is False)
    m = cs.get_memory("upsert_key")
    check("value обновлён", m.get("value") == "new")
    check("tier обновлён на long", m.get("tier") == "long")


def test_get_missing():
    print("get отсутствующего ключа")
    check("None для отсутствующего", cs.get_memory("no_such_key") is None)


def test_access_count_increments():
    print("access_count инкремент")
    cs.store_memory("hot_key", "v")
    cs.get_memory("hot_key")
    cs.get_memory("hot_key")
    m = cs.get_memory("hot_key")
    check("3 get'а → access_count=3", m.get("access_count") == 3)
    check("last_access не 0", m.get("last_access", 0) > 0)


def _clear_memory():
    """Полный сброс memory_items — изоляция между тестами консолидации."""
    with cs._get_conn() as conn:
        conn.execute("DELETE FROM memory_items")
        conn.commit()


def test_consolidate_age_short_to_medium():
    print("consolidate: возраст short→medium")
    _clear_memory()
    cs.store_memory("old_short", "v")
    cs.store_memory("fresh_short", "v")
    _backdate("old_short", 25 * 3600)  # старше short_ttl_hours=24
    res = cs.consolidate_memory(short_ttl_hours=24)
    check("1 запись повышена short→medium", res["short_to_medium"] == 1)
    check("0 medium→long", res["medium_to_long"] == 0)
    check("старая стала medium", cs.get_memory("old_short")["tier"] == "medium")
    check("свежая осталась short", cs.get_memory("fresh_short")["tier"] == "short")


def test_consolidate_freq_short_to_medium():
    print("consolidate: частота short→medium")
    _clear_memory()
    cs.store_memory("freq_short", "v")
    _set_access("freq_short", 5)
    res = cs.consolidate_memory(promote_accesses=3)
    check("частая short стала medium", cs.get_memory("freq_short")["tier"] == "medium")
    check("счётчик в результате 1", res["short_to_medium"] == 1)


def test_consolidate_medium_to_long():
    print("consolidate: medium→long")
    _clear_memory()
    cs.store_memory("old_med", "v", tier="medium")
    cs.store_memory("fresh_med", "v", tier="medium")
    _backdate("old_med", 8 * 86400)  # старше medium_ttl_days=7
    res = cs.consolidate_memory(medium_ttl_days=7)
    check("1 запись medium→long", res["medium_to_long"] == 1)
    check("старая стала long", cs.get_memory("old_med")["tier"] == "long")
    check("свежая осталась medium", cs.get_memory("fresh_med")["tier"] == "medium")


def test_consolidate_freq_medium_to_long():
    print("consolidate: частота medium→long")
    _clear_memory()
    cs.store_memory("freq_med", "v", tier="medium")
    _set_access("freq_med", 12)
    res = cs.consolidate_memory(long_accesses=10)
    check("частый medium стал long", cs.get_memory("freq_med")["tier"] == "long")
    check("счётчик medium_to_long=1", res["medium_to_long"] == 1)


def test_consolidate_empty():
    print("consolidate пустой БД")
    res = cs.consolidate_memory()
    check("нули", res == {"short_to_medium": 0, "medium_to_long": 0})


def test_promote_only_upward():
    print("promote только вверх")
    cs.store_memory("p1", "v", tier="short")
    check("short→medium → True", cs.promote_memory("p1", "medium") is True)
    check("теперь medium", cs.get_memory("p1")["tier"] == "medium")
    check("medium→long → True", cs.promote_memory("p1", "long") is True)
    check("long→medium (вниз) → False", cs.promote_memory("p1", "medium") is False)
    check("long→short (вниз) → False", cs.promote_memory("p1", "short") is False)
    check("тот же tier → False", cs.promote_memory("p1", "long") is False)
    check("нет ключа → False", cs.promote_memory("no_key", "long") is False)
    check("невалидный tier → False", cs.promote_memory("p1", "super") is False)
    check("итог long", cs.get_memory("p1")["tier"] == "long")


def test_stats_and_list():
    print("статистика и list")
    before = cs.memory_stats()
    before_total = len(cs.list_memory())
    cs.store_memory("s1", "v")
    cs.store_memory("s2", "v")
    cs.store_memory("m1", "v", tier="medium")
    cs.store_memory("l1", "v", tier="long")
    st = cs.memory_stats()
    check("short +2", st["short"] == before["short"] + 2)
    check("medium +1", st["medium"] == before["medium"] + 1)
    check("long +1", st["long"] == before["long"] + 1)
    all_items = cs.list_memory()
    check("list без фильтра +4", len(all_items) == before_total + 4)
    mediums = cs.list_memory(tier="medium")
    check("фильтр medium содержит m1", any(m["key"] == "m1" for m in mediums))
    check("фильтр невалидный → пусто", cs.list_memory(tier="bad") == [])


def test_cli_integration():
    print("CLI: mem-store/mem-get")
    db_path = Path(TMP) / "cli.db"
    env = dict(os.environ, AGI_CONTEXT_STORE_DB=str(db_path))
    r = subprocess.run(
        [sys.executable, "agi_context_store.py", "mem-store", "cli_key", "cli_val", "medium"],
        capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    check("mem-store rc=0", r.returncode == 0 and "stored" in r.stdout)
    r = subprocess.run(
        [sys.executable, "agi_context_store.py", "mem-get", "cli_key"],
        capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    ok = r.returncode == 0 and "cli_val" in r.stdout and "medium" in r.stdout
    check("mem-get вернул value+tier", ok)
    r = subprocess.run(
        [sys.executable, "agi_context_store.py", "mem-stats"],
        capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    check("mem-stats rc=0", r.returncode == 0 and "short" in r.stdout)


def test_report_has_memory():
    print("get_report содержит секцию памяти")
    cs.store_memory("rep_key", "v")
    rep = cs.get_report()
    check("в отчёте есть 🧠 Память", "Память" in rep)
    check("в отчёте есть тиры", "short" in rep and "long" in rep)


def test_audit_events():
    print("audit-события памяти")
    cs.store_memory("aud_key", "v")
    events = cs.get_audit(20)
    ops = [e["op"] for e in events]
    check("memory_store в логе", "memory_store" in ops)


def main():
    test_store_get_basic()
    test_store_validation()
    test_store_upsert()
    test_get_missing()
    test_access_count_increments()
    test_consolidate_age_short_to_medium()
    test_consolidate_freq_short_to_medium()
    test_consolidate_medium_to_long()
    test_consolidate_freq_medium_to_long()
    test_consolidate_empty()
    test_promote_only_upward()
    test_stats_and_list()
    test_cli_integration()
    test_report_has_memory()
    test_audit_events()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
