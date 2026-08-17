#!/usr/bin/env python3
"""agi_test_memory_history_source.py — тесты provenance: колонка source (цикл 38).

SELF_IMPROVE_2026-08-17 #2 (MemClaw): superseded-версии в memory_history
должны хранить ИСТОЧНИК записи (user/email/агент), а не только old/new
value. Защита от provenance collapse — известно КТО перезаписал факт.

Покрытие: source по умолчанию, валидация (пустой/не-строка/пробелы),
корректная запись при supersession, чтение через get_memory_history,
миграция старой БД (ALTER TABLE), фильтр по ключу, отчёт с источниками.
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_context_store as cs

TMP = tempfile.mkdtemp(prefix="agi_history_source_test_")
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


def _raw_rows(key=None):
    """Прямое чтение таблицы: key, old_value, old_tier, new_value, superseded_at, source."""
    with cs._get_conn() as conn:
        if key is None:
            rows = conn.execute(
                "SELECT key, old_value, old_tier, new_value, superseded_at, source"
                " FROM memory_history ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, old_value, old_tier, new_value, superseded_at, source"
                " FROM memory_history WHERE key = ? ORDER BY id", (key,)
            ).fetchall()
    return [dict(r) for r in rows]


def _cols():
    with cs._get_conn() as conn:
        return [r["name"] for r in conn.execute("PRAGMA table_info(memory_history)")]


def test_default_source_is_agent():
    print("source по умолчанию = agent")
    cs.store_memory("src_default", "v1", "short")
    cs.store_memory("src_default", "v2", "short")
    rows = _raw_rows("src_default")
    check("1 superseded строка", len(rows) == 1)
    check("source == 'agent'", rows[0]["source"] == "agent")


def test_explicit_source_recorded():
    print("явный source записывается")
    cs.store_memory("src_user", "v1", "short")
    cs.store_memory("src_user", "v2", "short", source="user")
    rows = _raw_rows("src_user")
    check("source == 'user'", rows[0]["source"] == "user")


def test_email_source():
    print("source email")
    cs.store_memory("src_mail", "v1", "short")
    cs.store_memory("src_mail", "v2", "short", source="email")
    rows = _raw_rows("src_mail")
    check("source == 'email'", rows[0]["source"] == "email")


def test_invalid_source_falls_back_to_agent():
    print("невалидный source → agent, store НЕ падает")
    r1 = cs.store_memory("src_bad1", "v1", "short", source="")
    r2 = cs.store_memory("src_bad1", "v2", "short", source=None)
    r3 = cs.store_memory("src_bad1", "v3", "short", source=123)
    rows = _raw_rows("src_bad1")
    check("store: новый ключ True, обновления False", r1 is True and r2 is False and r3 is False)
    check("2 superseded строки", len(rows) == 2)
    check("все source == 'agent'", all(r["source"] == "agent" for r in rows))


def test_source_stripped():
    print("source с пробелами обрезается")
    cs.store_memory("src_sp", "v1", "short")
    cs.store_memory("src_sp", "v2", "short", source="  user  ")
    rows = _raw_rows("src_sp")
    check("source == 'user' (без пробелов)", rows[0]["source"] == "user")


def test_multiple_sources_distinct():
    print("разные источники — отдельные строки")
    cs.store_memory("src_multi", "v1", "short")
    cs.store_memory("src_multi", "v2", "short", source="user")
    cs.store_memory("src_multi", "v3", "short", source="email")
    rows = _raw_rows("src_multi")
    check("2 строки истории", len(rows) == 2)
    check("порядок ASC: user раньше, email позже", rows[0]["source"] == "user" and rows[1]["source"] == "email")


def test_get_memory_history_includes_source():
    print("get_memory_history возвращает source")
    cs.store_memory("src_api", "v1", "short")
    cs.store_memory("src_api", "v2", "short", source="email")
    rows = cs.get_memory_history("src_api")
    check("1 строка", len(rows) == 1)
    check("поле source есть", "source" in rows[0])
    check("source == 'email'", rows[0]["source"] == "email")
    check("старые поля на месте", "old_value" in rows[0] and "new_value" in rows[0] and "superseded_at" in rows[0])


def test_migration_old_db_gets_source_column():
    print("миграция: старая БД без source → ALTER TABLE")
    old_db = Path(TMP) / "old_schema.db"
    conn = sqlite3.connect(old_db)
    conn.execute(
        "CREATE TABLE memory_history ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " key TEXT NOT NULL, old_value TEXT NOT NULL, old_tier TEXT NOT NULL,"
        " new_value TEXT NOT NULL, superseded_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO memory_history (key, old_value, old_tier, new_value, superseded_at)"
        " VALUES ('legacy', 'a', 'short', 'b', 1000.0)"
    )
    conn.commit()
    conn.close()

    # подменяем БД на старую схему
    cs.DB_PATH = old_db
    cs._ensure_db()
    cols = _cols()
    check("колонка source добавлена", "source" in cols)
    rows = _raw_rows("legacy")
    check("старая строка не потеряна", len(rows) == 1)
    check("legacy source == 'agent' (DEFAULT)", rows[0]["source"] == "agent")
    # после миграции новые записи работают
    cs.store_memory("legacy", "b", "short")
    cs.store_memory("legacy", "c", "short", source="email")
    rows2 = _raw_rows("legacy")
    check("новая supersession записана", len(rows2) == 2)
    check("legacy->b: agent, b->c: email", rows2[0]["source"] == "agent" and rows2[1]["source"] == "email")
    cs.DB_PATH = Path(TMP) / "test.db"  # вернуть основную БД


def test_stats_unchanged_shape():
    print("memory_history_stats не сломан")
    cs.store_memory("src_stats", "v1", "short")
    cs.store_memory("src_stats", "v2", "short", source="user")
    st = cs.memory_history_stats()
    check("total >= 1", st["total"] >= 1)
    check("keys >= 1", st["keys"] >= 1)
    check("только total/keys", set(st.keys()) == {"total", "keys"})


def test_report_shows_sources():
    print("get_report упоминает источник")
    cs.store_memory("src_report", "v1", "short")
    cs.store_memory("src_report", "v2", "short", source="user")
    rep = cs.get_report()
    check("отчёт содержит 'источник'", "источник" in rep.lower() or "source" in rep.lower())


def main():
    print("=== agi_test_memory_history_source.py (provenance source) ===")
    test_default_source_is_agent()
    test_explicit_source_recorded()
    test_email_source()
    test_invalid_source_falls_back_to_agent()
    test_source_stripped()
    test_multiple_sources_distinct()
    test_get_memory_history_includes_source()
    test_migration_old_db_gets_source_column()
    test_stats_unchanged_shape()
    test_report_shows_sources()
    print(f"\nRESULT: PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
