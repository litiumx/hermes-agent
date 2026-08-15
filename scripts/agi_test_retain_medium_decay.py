#!/usr/bin/env python3
"""agi_test_retain_medium_decay.py — тесты decay medium-тира + mem-maintain (цикл 29).

Покрытие: retain_memory — medium decay (возраст И слабый доступ, AND → demote
до short, факт НЕ удаляется), свежие/частые medium выживают, never-accessed,
отключение по параметрам, short/long не задеваются decay, updated_at сброс
(нет мгновенной ре-консолидации), audit с demoted_medium, комбо long+medium,
CLI mem-maintain (retain+consolidate, JSON) через subprocess.
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

TMP = tempfile.mkdtemp(prefix="agi_decay_test_")
cs.DB_PATH = Path(TMP) / "test.db"

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} {extra}")


def _clear_memory():
    cs._ensure_db()
    with cs._get_conn() as conn:
        conn.execute("DELETE FROM memory_items")
        conn.commit()


def _set_access(key, n):
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE memory_items SET access_count = ? WHERE key = ?", (n, key)
        )
        conn.commit()


def _set_last_access(key, seconds_ago):
    with cs._get_conn() as conn:
        conn.execute(
            "UPDATE memory_items SET last_access = ? WHERE key = ?",
            (time.time() - seconds_ago, key),
        )
        conn.commit()


def _tier_of(key):
    m = cs.get_memory(key)
    return m["tier"] if m else None


def _retain_events():
    return [e for e in cs.get_audit(200) if e["op"] == "memory_retain"]


def test_decay_empty():
    print("decay: пустая БД")
    _clear_memory()
    res = cs.retain_memory()
    check("demoted_medium=0",
          res == {"evicted_stale": 0, "evicted_cap": 0,
                  "demoted_medium": 0, "long_left": 0}, str(res))


def test_decay_fresh_survives():
    print("decay: свежий medium (доступ недавно) выживает")
    _clear_memory()
    cs.store_memory("med_fresh", "v", tier="medium")
    _set_last_access("med_fresh", 3600)  # 1 час назад
    _set_access("med_fresh", 0)
    res = cs.retain_memory()
    check("demoted=0", res["demoted_medium"] == 0, str(res))
    check("tier=medium", _tier_of("med_fresh") == "medium")


def test_decay_stale_low_access_demotes():
    print("decay: старый medium + слабый доступ → demote до short")
    _clear_memory()
    cs.store_memory("med_stale", "v", tier="medium")
    _set_last_access("med_stale", 400 * 86400)
    _set_access("med_stale", 0)
    res = cs.retain_memory()
    check("demoted=1", res["demoted_medium"] == 1, str(res))
    check("tier=short (факт жив)", _tier_of("med_stale") == "short")
    check("long не тронут", res["long_left"] == 0)


def test_decay_stale_high_access_survives():
    print("decay: старый medium + частый доступ → выживает (AND)")
    _clear_memory()
    cs.store_memory("med_used", "v", tier="medium")
    _set_last_access("med_used", 400 * 86400)
    _set_access("med_used", 5)
    res = cs.retain_memory()
    check("demoted=0", res["demoted_medium"] == 0, str(res))
    check("tier=medium", _tier_of("med_used") == "medium")


def test_decay_never_accessed():
    print("decay: never-accessed medium (last_access=0) + 0 доступов → demote")
    _clear_memory()
    cs.store_memory("med_never", "v", tier="medium")  # last_access=0, access=0
    res = cs.retain_memory()
    check("demoted=1", res["demoted_medium"] == 1, str(res))
    check("tier=short", _tier_of("med_never") == "short")


def test_decay_disabled_ttl():
    print("decay: medium_ttl_days=0 → decay выключен")
    _clear_memory()
    cs.store_memory("med_off_ttl", "v", tier="medium")
    _set_last_access("med_off_ttl", 400 * 86400)
    _set_access("med_off_ttl", 0)
    res = cs.retain_memory(medium_ttl_days=0, min_medium_accesses=2)
    check("demoted=0", res["demoted_medium"] == 0, str(res))
    check("tier=medium", _tier_of("med_off_ttl") == "medium")


def test_decay_disabled_min_access():
    print("decay: min_medium_accesses=0 → decay выключен")
    _clear_memory()
    cs.store_memory("med_off_min", "v", tier="medium")
    _set_last_access("med_off_min", 400 * 86400)
    _set_access("med_off_min", 0)
    res = cs.retain_memory(medium_ttl_days=7, min_medium_accesses=0)
    check("demoted=0", res["demoted_medium"] == 0, str(res))
    check("tier=medium", _tier_of("med_off_min") == "medium")


def test_decay_short_not_touched():
    print("decay: stale short НЕ трогается (decay только medium)")
    _clear_memory()
    cs.store_memory("short_stale", "v", tier="short")
    _set_last_access("short_stale", 400 * 86400)
    _set_access("short_stale", 0)
    res = cs.retain_memory()
    check("demoted=0", res["demoted_medium"] == 0, str(res))
    check("tier=short", _tier_of("short_stale") == "short")


def test_decay_updates_timestamp_no_reconsolidate():
    print("decay: updated_at сброшен → consolidate не ре-промоутит сразу")
    _clear_memory()
    cs.store_memory("med_reset", "v", tier="medium")
    _set_last_access("med_reset", 400 * 86400)
    _set_access("med_reset", 0)
    cs.retain_memory()
    before = cs.get_memory("med_reset")["updated_at"]
    check("updated_at свежий (>= now-60)",
          before >= time.time() - 60, str(before))
    cs.consolidate_memory()  # свежий updated_at + access 0 → не промоутится
    check("tier всё ещё short", _tier_of("med_reset") == "short")


def test_decay_audit():
    print("decay: audit-событие с demoted_medium, no-op без события")
    _clear_memory()
    before = len(_retain_events())
    cs.retain_memory()  # no-op
    check("no-op без audit", len(_retain_events()) == before)
    cs.store_memory("med_audit", "v", tier="medium")
    _set_last_access("med_audit", 400 * 86400)
    _set_access("med_audit", 0)
    cs.retain_memory()
    events = _retain_events()
    check("одно memory_retain событие", len(events) == before + 1)
    payload = events[-1]["payload"]
    check("payload с demoted_medium=1", payload.get("demoted_medium") == 1,
          str(payload))


def test_decay_combined_with_long():
    print("decay: medium decay + long stale-эвикция в одном вызове")
    _clear_memory()
    cs.store_memory("combo_med", "v", tier="medium")
    _set_last_access("combo_med", 400 * 86400)
    _set_access("combo_med", 0)
    cs.store_memory("combo_long", "v", tier="long")
    _set_last_access("combo_long", 400 * 86400)
    _set_access("combo_long", 0)
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("demoted=1", res["demoted_medium"] == 1, str(res))
    check("evicted_stale=1", res["evicted_stale"] == 1, str(res))
    check("medium → short", _tier_of("combo_med") == "short")
    check("long удалён", _tier_of("combo_long") is None)


def test_maintain_cli_defaults():
    print("CLI: mem-maintain с дефолтами — JSON retain+consolidate")
    db_path = Path(TMP) / "cli_maintain.db"
    env = dict(os.environ, AGI_CONTEXT_STORE_DB=str(db_path))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "agi_context_store.py")
    r = subprocess.run([sys.executable, script, "mem-store",
                        "cli_med", "v", "medium"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli store ok", r.returncode == 0, r.stderr)
    cs.DB_PATH = db_path
    _set_last_access("cli_med", 400 * 86400)
    _set_access("cli_med", 0)
    r = subprocess.run([sys.executable, script, "mem-maintain"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli maintain ok", r.returncode == 0, r.stderr)
    out = json.loads(r.stdout)
    check("JSON с retain", "retain" in out, r.stdout)
    check("JSON с consolidate", "consolidate" in out, r.stdout)
    check("retain.demoted_medium=1",
          out["retain"].get("demoted_medium") == 1, str(out))
    check("consolidate.medium_to_long=0",
          out["consolidate"].get("medium_to_long") == 0, str(out))


def test_maintain_cli_decay_disabled():
    print("CLI: mem-maintain с medium_ttl_days=0 — decay выключен")
    db_path = Path(TMP) / "cli_maintain_off.db"
    env = dict(os.environ, AGI_CONTEXT_STORE_DB=str(db_path))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "agi_context_store.py")
    subprocess.run([sys.executable, script, "mem-store",
                    "cli_off", "v", "medium"],
                   capture_output=True, text=True, env=env, timeout=60)
    cs.DB_PATH = db_path
    _set_last_access("cli_off", 400 * 86400)
    _set_access("cli_off", 0)
    r = subprocess.run([sys.executable, script, "mem-maintain", "200", "30",
                        "5", "0", "2"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli maintain ok", r.returncode == 0, r.stderr)
    out = json.loads(r.stdout)
    check("demoted=0", out["retain"].get("demoted_medium") == 0, str(out))


def test_maintain_cli_consolidates():
    print("CLI: mem-maintain консолидирует short→medium")
    db_path = Path(TMP) / "cli_maintain_cons.db"
    env = dict(os.environ, AGI_CONTEXT_STORE_DB=str(db_path))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "agi_context_store.py")
    subprocess.run([sys.executable, script, "mem-store",
                    "cli_old_short", "v", "short"],
                   capture_output=True, text=True, env=env, timeout=60)
    cs.DB_PATH = db_path
    with cs._get_conn() as conn:  # возраст 2 дня → short→medium по consolidate
        conn.execute("UPDATE memory_items SET updated_at = ? WHERE key = ?",
                     (time.time() - 2 * 86400, "cli_old_short"))
        conn.commit()
    r = subprocess.run([sys.executable, script, "mem-maintain"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli maintain ok", r.returncode == 0, r.stderr)
    out = json.loads(r.stdout)
    check("consolidate.short_to_medium=1",
          out["consolidate"].get("short_to_medium") == 1, str(out))


if __name__ == "__main__":
    test_decay_empty()
    test_decay_fresh_survives()
    test_decay_stale_low_access_demotes()
    test_decay_stale_high_access_survives()
    test_decay_never_accessed()
    test_decay_disabled_ttl()
    test_decay_disabled_min_access()
    test_decay_short_not_touched()
    test_decay_updates_timestamp_no_reconsolidate()
    test_decay_audit()
    test_decay_combined_with_long()
    test_maintain_cli_defaults()
    test_maintain_cli_decay_disabled()
    test_maintain_cli_consolidates()

    print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
