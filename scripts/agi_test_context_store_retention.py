#!/usr/bin/env python3
"""agi_test_context_store_retention.py — тесты ретеншна long-тира (цикл 24).

Покрытие: retain_memory — stale-эвикция (возраст И слабый доступ, AND),
never-accessed (last_access=0), свежие выживают, cap-эвикция (лимит размера,
самые старые по last_access), отключение по параметрам, только long-тир,
audit-событие, CLI mem-retain через subprocess.
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

TMP = tempfile.mkdtemp(prefix="agi_retention_test_")
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


def _long_count():
    return cs.memory_stats()["long"]


def _retain_events():
    return [e for e in cs.get_audit(200) if e["op"] == "memory_retain"]


def test_retain_empty():
    print("retain: пустая БД")
    _clear_memory()
    res = cs.retain_memory()
    check("все счётчики 0",
          res == {"evicted_stale": 0, "evicted_cap": 0,
                  "demoted_medium": 0, "long_left": 0})


def test_retain_fresh_survives():
    print("retain: свежий long (возраст < ttl) выживает")
    _clear_memory()
    cs.store_memory("fresh_low", "v", tier="long")   # last_access=0, access=0
    _set_last_access("fresh_low", 3600)              # читали час назад
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=0", res["evicted_stale"] == 0)
    check("long_left=1", res["long_left"] == 1)
    check("ключ на месте", cs.get_memory("fresh_low") is not None)


def test_retain_stale_low_access_evicted():
    print("retain: старый И слабый доступ → эвикция")
    _clear_memory()
    cs.store_memory("stale_weak", "v", tier="long")
    _set_last_access("stale_weak", 40 * 86400)  # 40 дней назад
    _set_access("stale_weak", 2)                # access < min=5
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=1", res["evicted_stale"] == 1)
    check("long_left=0", res["long_left"] == 0)
    check("ключ удалён", cs.get_memory("stale_weak") is None)


def test_retain_stale_high_access_survives():
    print("retain: старый НО частый доступ → выживает (AND)")
    _clear_memory()
    cs.store_memory("stale_strong", "v", tier="long")
    _set_last_access("stale_strong", 40 * 86400)
    _set_access("stale_strong", 10)  # access >= min=5
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=0", res["evicted_stale"] == 0)
    check("выжил", cs.get_memory("stale_strong") is not None)


def test_retain_never_accessed_evicted():
    print("retain: never-accessed (last_access=0) → эвикция")
    _clear_memory()
    cs.store_memory("never_used", "v", tier="long")  # last_access=0, access=0
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=1", res["evicted_stale"] == 1)
    check("удалён", cs.get_memory("never_used") is None)


def test_retain_recent_access_low_count_survives():
    print("retain: недавний доступ + низкий счётчик → выживает")
    _clear_memory()
    cs.store_memory("recent_weak", "v", tier="long")
    _set_last_access("recent_weak", 300)  # 5 минут назад
    _set_access("recent_weak", 1)
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=0", res["evicted_stale"] == 0)
    check("выжил", cs.get_memory("recent_weak") is not None)


def test_retain_cap_evicts_oldest():
    print("retain: cap — удаляются самые старые по last_access")
    _clear_memory()
    # 5 long-фактов, свежие (в пределах ttl), частые (проходят stale-guard)
    for i in range(1, 6):
        cs.store_memory(f"cap_k{i}", "v", tier="long")
        _set_last_access(f"cap_k{i}", i * 3600)  # 1ч..5ч назад
        _set_access(f"cap_k{i}", 10)
    res = cs.retain_memory(max_long=3, long_ttl_days=30, min_long_accesses=5)
    check("cap=2", res["evicted_cap"] == 2)
    check("stale=0 (все свежие)", res["evicted_stale"] == 0)
    check("long_left=3", res["long_left"] == 3)
    check("самые свежие выжили", all(
        cs.get_memory(f"cap_k{i}") is not None for i in (1, 2, 3)))
    check("самые старые удалены", all(
        cs.get_memory(f"cap_k{i}") is None for i in (4, 5)))


def test_retain_cap_disabled():
    print("retain: max_long<=0 → cap отключён")
    _clear_memory()
    for i in range(8):
        cs.store_memory(f"nocap_k{i}", "v", tier="long")
        _set_last_access(f"nocap_k{i}", (i + 1) * 3600)
        _set_access(f"nocap_k{i}", 10)
    res = cs.retain_memory(max_long=0)
    check("cap=0", res["evicted_cap"] == 0)
    check("все 8 на месте", _long_count() == 8)


def test_retain_stale_disabled():
    print("retain: ttl<=0 → stale-чистка отключена")
    _clear_memory()
    cs.store_memory("no_stale", "v", tier="long")
    _set_last_access("no_stale", 400 * 86400)
    _set_access("no_stale", 0)
    res = cs.retain_memory(long_ttl_days=0, min_long_accesses=5)
    check("stale=0", res["evicted_stale"] == 0)
    check("выжил", cs.get_memory("no_stale") is not None)


def test_retain_min_accesses_disabled():
    print("retain: min_long_accesses<=0 → stale-чистка отключена")
    _clear_memory()
    cs.store_memory("no_min", "v", tier="long")
    _set_last_access("no_min", 400 * 86400)
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=0)
    check("stale=0", res["evicted_stale"] == 0)
    check("выжил", cs.get_memory("no_min") is not None)


def test_retain_only_long_tier():
    print("retain: short не трогается; stale medium decay→short (цикл 29)")
    _clear_memory()
    cs.store_memory("old_medium", "v", tier="medium")
    cs.store_memory("old_short", "v", tier="short")
    _set_last_access("old_medium", 400 * 86400)
    _set_access("old_medium", 0)
    res = cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    check("stale=0", res["evicted_stale"] == 0)
    check("medium decay→short",
          cs.get_memory("old_medium")["tier"] == "short")
    check("short на месте", cs.get_memory("old_short")["tier"] == "short")


def test_retain_audit():
    print("retain: audit-событие при эвикции, тишина при no-op")
    _clear_memory()
    before = len(_retain_events())
    cs.retain_memory()  # no-op
    check("no-op без audit", len(_retain_events()) == before)
    cs.store_memory("audit_victim", "v", tier="long")
    _set_last_access("audit_victim", 400 * 86400)
    _set_access("audit_victim", 0)
    cs.retain_memory(long_ttl_days=30, min_long_accesses=5)
    events = _retain_events()
    check("одно memory_retain событие", len(events) == before + 1)
    payload = events[-1]["payload"]
    check("payload с evicted_stale=1", payload.get("evicted_stale") == 1)


def test_retain_combined_counts():
    print("retain: stale+cap вместе, long_left корректен")
    _clear_memory()
    # 3 stale (слабый доступ, давно) + 5 свежих частых
    for i in range(3):
        cs.store_memory(f"mix_stale{i}", "v", tier="long")
        _set_last_access(f"mix_stale{i}", 100 * 86400)
        _set_access(f"mix_stale{i}", 0)
    for i in range(5):
        cs.store_memory(f"mix_fresh{i}", "v", tier="long")
        _set_last_access(f"mix_fresh{i}", (i + 1) * 3600)
        _set_access(f"mix_fresh{i}", 10)
    res = cs.retain_memory(max_long=3, long_ttl_days=30, min_long_accesses=5)
    check("stale=3", res["evicted_stale"] == 3)
    check("cap=2 (5 свежих → лимит 3)", res["evicted_cap"] == 2)
    check("long_left=3", res["long_left"] == 3)


def test_retain_cli():
    print("retain: CLI mem-retain через subprocess")
    db_path = Path(TMP) / "cli.db"
    env = dict(os.environ, AGI_CONTEXT_STORE_DB=str(db_path))
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "agi_context_store.py")
    r = subprocess.run([sys.executable, script, "mem-store", "cli_stale", "v", "long"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli store ok", r.returncode == 0)
    # backdate через in-process коннект к той же БД
    cs.DB_PATH = db_path
    _set_last_access("cli_stale", 3 * 86400)
    _set_access("cli_stale", 0)
    r = subprocess.run([sys.executable, script, "mem-retain", "10", "1", "5"],
                       capture_output=True, text=True, env=env, timeout=60)
    check("cli retain ok", r.returncode == 0)
    check("вывод stale=1", "stale=1" in r.stdout)
    check("вывод long_left=0", "long_left=0" in r.stdout)


if __name__ == "__main__":
    test_retain_empty()
    test_retain_fresh_survives()
    test_retain_stale_low_access_evicted()
    test_retain_stale_high_access_survives()
    test_retain_never_accessed_evicted()
    test_retain_recent_access_low_count_survives()
    test_retain_cap_evicts_oldest()
    test_retain_cap_disabled()
    test_retain_stale_disabled()
    test_retain_min_accesses_disabled()
    test_retain_only_long_tier()
    test_retain_audit()
    test_retain_combined_counts()
    test_retain_cli()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
