#!/usr/bin/env python3
"""agi_context_store.py — SQLite-backed контекст между рестартами.

Преимущества над JSON:
- WAL-режим: конкурентные чтения без блокировок
- Дедупликация pending_tasks по хешу
- Aging: авто-удаление задач старше 48 часов
- История: запросы за любой период
- Атомарность: защита от битых JSON при падении
"""
import json, os, sqlite3, time, hashlib
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.environ.get(
    "AGI_CONTEXT_STORE_DB",
    os.path.join(os.environ.get("HERMES_HOME", "/root/.hermes"),
                 "data/context_store.db")))
TASK_TTL_HOURS = 48  # авто-удаление старых задач
MAX_SESSIONS = 200   # retention: сколько последних сессий хранить
SNAPSHOT_TTL_DAYS = 7  # retention: снапшоты старше — на удаление
MEMORY_TIERS = ("short", "medium", "long")  # Saucedo Multi-Tier Memory


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                last_task TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                session_phase TEXT DEFAULT 'unknown',
                tool_call_count INTEGER DEFAULT 0,
                swarm_size INTEGER DEFAULT 3,
                last_known_good INTEGER DEFAULT 1,
                active_projects TEXT DEFAULT '[]',
                user_preferences TEXT DEFAULT '{}',
                modified_files TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT UNIQUE NOT NULL,
                task TEXT NOT NULL,
                created_at REAL NOT NULL,
                priority INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                snapshot_json TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        # Append-only audit лог (OptMem-паттерн): строки НИКОГДА не
        # редактируются, summary пересобирается из лога (rebuild_summary).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                op TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_summary (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                rebuilt_at REAL NOT NULL,
                total_events INTEGER NOT NULL,
                last_id INTEGER NOT NULL,
                op_counts_json TEXT NOT NULL
            )
        """)
        # Saucedo Multi-Tier Memory: факты с тирами short/medium/long.
        # Тир повышается consolidate_memory (возраст/частота) или promote_memory.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'short',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_access REAL NOT NULL DEFAULT 0
            )
        """)
        # Temporal supersession (MemClaw): append-only история перезаписей.
        # Старый value НЕ удаляется при update — хранится как superseded
        # версия с provenance (что было, чем заменено, когда). Переживает
        # eviction из memory_items (retain_memory) — защита от provenance
        # collapse (инцидент MemGhost).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                old_value TEXT NOT NULL,
                old_tier TEXT NOT NULL,
                new_value TEXT NOT NULL,
                superseded_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_hash ON pending_tasks(task_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON pending_tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_tier ON memory_items(tier)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memhist_key ON memory_history(key)")
        conn.commit()


@contextmanager
def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # WAL допускает параллельных читателей, но писатели могут получить
        # SQLITE_BUSY при конкурентном доступе (cron + gateway) — ждём до 3с
        conn.execute("PRAGMA busy_timeout=3000")
        yield conn
    finally:
        conn.close()


def _task_hash(task: str) -> str:
    return hashlib.sha256(task.strip().lower().encode()).hexdigest()[:16]


# --- Append-only audit лог (OptMem-паттерн) ---
# Лог — источник истины: строки только добавляются, никогда не меняются.
# summary — пересобираемый кэш (rebuild_summary) из лога.

def append_audit(op: str, payload: dict = None) -> int:
    """Добавить событие в append-only лог. Возвращает id (0 — если op невалиден).

    payload сериализуется в JSON; не-JSON-сериализуемое → {}."""
    if not isinstance(op, str) or not op.strip():
        return 0
    _ensure_db()
    try:
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        payload_json = "{}"
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO audit_log (ts, op, payload_json) VALUES (?, ?, ?)",
            (time.time(), op.strip(), payload_json),
        )
        conn.commit()
        return cursor.lastrowid


def get_audit(limit: int = 50) -> list[dict]:
    """Последние события лога (новые первыми). limit<=0 → пустой список."""
    _ensure_db()
    if limit <= 0:
        return []
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, ts, op, payload_json FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except (ValueError, TypeError):
                payload = {}
            out.append({"id": r["id"], "ts": r["ts"], "op": r["op"], "payload": payload})
        return out


def _read_log_counts(conn) -> tuple:
    """(total, last_id, op_counts) из лога — пересобираемо из источника истины."""
    total = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
    last_id = conn.execute("SELECT MAX(id) as m FROM audit_log").fetchone()["m"] or 0
    op_counts = {}
    for r in conn.execute(
        "SELECT op, COUNT(*) as c FROM audit_log GROUP BY op"
    ).fetchall():
        op_counts[r["op"]] = r["c"]
    return total, last_id, op_counts


def rebuild_summary() -> dict:
    """Пересобрать summary из лога (идемпотентно). Лог не трогается."""
    _ensure_db()
    with _get_conn() as conn:
        total, last_id, op_counts = _read_log_counts(conn)
        conn.execute(
            """INSERT OR REPLACE INTO audit_summary
               (id, rebuilt_at, total_events, last_id, op_counts_json)
               VALUES (1, ?, ?, ?, ?)""",
            (time.time(), total, last_id, json.dumps(op_counts, ensure_ascii=False)),
        )
        conn.commit()
    return get_audit_summary()


def get_audit_summary() -> dict:
    """Текущий summary (без пересборки). Пустой БД → нули."""
    _ensure_db()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM audit_summary WHERE id = 1").fetchone()
    if not row:
        return {"total_events": 0, "last_id": 0, "op_counts": {},
                "rebuilt_at": 0.0, "fresh": True}
    try:
        op_counts = json.loads(row["op_counts_json"])
    except (ValueError, TypeError):
        op_counts = {}
    return {"total_events": row["total_events"], "last_id": row["last_id"],
            "op_counts": op_counts, "rebuilt_at": row["rebuilt_at"], "fresh": True}


def audit_integrity() -> dict:
    """Проверка append-only инварианта.

    - ok=False если любая строка лога повреждена (не-JSON payload)
    - stale=True если лог вырос после последнего rebuild (summary отстаёт,
      но это НЕ коррупция — summary пересобираем)
    """
    _ensure_db()
    issues = []
    log_total = 0
    bad_payload = 0
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, payload_json FROM audit_log ORDER BY id"
        ).fetchall()
        log_total = len(rows)
        for r in rows:
            try:
                json.loads(r["payload_json"])
            except (ValueError, TypeError):
                bad_payload += 1
                issues.append(f"row {r['id']}: payload не-JSON")
        summary = conn.execute(
            "SELECT total_events, last_id FROM audit_summary WHERE id = 1"
        ).fetchone()

    summary_events = summary["total_events"] if summary else 0
    summary_last_id = summary["last_id"] if summary else 0
    stale = log_total > summary_events or (log_total > 0 and summary is None)
    ok = bad_payload == 0
    return {
        "ok": ok,
        "stale": stale,
        "log_events": log_total,
        "summary_events": summary_events,
        "summary_last_id": summary_last_id,
        "issues": issues,
    }


def save_context(context: dict) -> int:
    """Сохранить контекст сессии. Возвращает session_id."""
    _ensure_db()
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO sessions
               (timestamp, last_task, last_error, session_phase,
                tool_call_count, swarm_size, last_known_good,
                active_projects, user_preferences, modified_files)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                context.get("last_task", ""),
                context.get("last_error", ""),
                context.get("session_phase", "unknown"),
                context.get("tool_call_count", 0),
                context.get("swarm_size", 3),
                1 if context.get("last_known_good", True) else 0,
                json.dumps(context.get("active_projects", [])),
                json.dumps(context.get("user_preferences", {})),
                json.dumps(context.get("modified_files", [])),
            ),
        )
        session_id = cursor.lastrowid

        # Сохраняем pending_tasks с дедупликацией (пустые/не-строки пропускаем)
        for task in context.get("pending_tasks", []):
            if not isinstance(task, str) or not task.strip():
                continue
            th = _task_hash(task)
            conn.execute(
                """INSERT OR IGNORE INTO pending_tasks
                   (task_hash, task, created_at, priority, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (th, task, time.time(), 0, "session"),
            )

        # Сохраняем полный снимок для снапшотов
        if context.get("session_phase") in ("complete", "interrupted", "error"):
            conn.execute(
                "INSERT INTO context_snapshots (session_id, timestamp, snapshot_json) VALUES (?, ?, ?)",
                (session_id, time.time(), json.dumps(context, ensure_ascii=False)),
            )

        conn.commit()
        # Audit: лог — источник истины для всех мутаций
        append_audit("session_save", {"session_id": session_id,
                                      "phase": context.get("session_phase", "unknown")})
        return session_id


def load_context(session_id: int = None) -> dict:
    """Загрузить последний контекст (или конкретную сессию)."""
    _ensure_db()
    with _get_conn() as conn:
        if session_id is not None:  # явный id=0 не должен подменяться "последней"
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM sessions ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()

        if not row:
            return {}

        ctx = dict(row)
        ctx["active_projects"] = json.loads(ctx.get("active_projects", "[]"))
        ctx["user_preferences"] = json.loads(ctx.get("user_preferences", "{}"))
        ctx["modified_files"] = json.loads(ctx.get("modified_files", "[]"))

        # Загружаем активные pending_tasks (не старше TTL)
        cutoff = time.time() - TASK_TTL_HOURS * 3600
        tasks = conn.execute(
            "SELECT task, priority, created_at FROM pending_tasks WHERE created_at > ? ORDER BY priority DESC, created_at DESC",
            (cutoff,),
        ).fetchall()
        ctx["pending_tasks"] = [t["task"] for t in tasks]

        return ctx


def add_pending_task(task: str, priority: int = 0, source: str = "manual") -> bool:
    """Добавить задачу с дедупликацией. True если новая."""
    if not isinstance(task, str) or not task.strip():
        return False
    _ensure_db()
    th = _task_hash(task)
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO pending_tasks
               (task_hash, task, created_at, priority, source)
               VALUES (?, ?, ?, ?, ?)""",
            (th, task, time.time(), priority, source),
        )
        conn.commit()
        added = cursor.rowcount > 0
        if added:
            append_audit("task_add", {"task": task[:80], "priority": priority})
        return added


def remove_pending_task(task: str) -> bool:
    """Удалить задачу (по хешу)."""
    _ensure_db()
    th = _task_hash(task)
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM pending_tasks WHERE task_hash = ?", (th,))
        conn.commit()
        removed = cursor.rowcount > 0
        if removed:
            append_audit("task_remove", {"task": task[:80]})
        return removed


def age_out_tasks() -> int:
    """Удалить задачи старше TTL. Возвращает количество удалённых."""
    _ensure_db()
    cutoff = time.time() - TASK_TTL_HOURS * 3600
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM pending_tasks WHERE created_at < ?", (cutoff,))
        conn.commit()
        n = cursor.rowcount
        if n > 0:
            append_audit("tasks_age_out", {"count": n})
        return n


def prune_old(max_sessions: int = MAX_SESSIONS, snapshot_days: int = SNAPSHOT_TTL_DAYS) -> dict:
    """Retention: обрезать таблицы до разумных размеров.

    - sessions: оставить max_sessions последних (по timestamp), остальные удалить
      вместе со ссылающимися снапшотами (FK без ON DELETE CASCADE)
    - context_snapshots: удалить снапшоты старше snapshot_days

    Возвращает {'sessions': n, 'snapshots': n}.
    """
    _ensure_db()
    removed_sessions = removed_snapshots = 0
    with _get_conn() as conn:
        # id'шники сессий вне retention-окна
        stale = conn.execute(
            """SELECT id FROM sessions
               WHERE id NOT IN (
                   SELECT id FROM sessions ORDER BY timestamp DESC LIMIT ?
               )""",
            (max_sessions,),
        ).fetchall()
        stale_ids = [r["id"] for r in stale]
        if stale_ids:
            placeholders = ",".join("?" * len(stale_ids))
            removed_snapshots += conn.execute(
                f"DELETE FROM context_snapshots WHERE session_id IN ({placeholders})", stale_ids
            ).rowcount
            removed_sessions = conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})", stale_ids
            ).rowcount

        # снапшоты старше TTL (включая сирот от удалённых сессий)
        snap_cutoff = time.time() - snapshot_days * 86400
        removed_snapshots += conn.execute(
            "DELETE FROM context_snapshots WHERE timestamp < ?", (snap_cutoff,)
        ).rowcount
        conn.commit()

    if removed_sessions or removed_snapshots:
        append_audit("prune", {"sessions": removed_sessions,
                               "snapshots": removed_snapshots})

    return {"sessions": removed_sessions, "snapshots": removed_snapshots}


def get_session_history(hours: int = 24) -> list[dict]:
    """Получить историю сессий за период."""
    _ensure_db()
    cutoff = time.time() - hours * 3600
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, last_task, session_phase FROM sessions WHERE timestamp > ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict:
    """Статистика хранилища."""
    _ensure_db()
    with _get_conn() as conn:
        total_sessions = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        active_tasks = conn.execute("SELECT COUNT(*) as c FROM pending_tasks").fetchone()["c"]
        snapshots = conn.execute("SELECT COUNT(*) as c FROM context_snapshots").fetchone()["c"]
        audit_events = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    return {
        "total_sessions": total_sessions,
        "active_tasks": active_tasks,
        "snapshots": snapshots,
        "audit_events": audit_events,
        "db_size_kb": db_size // 1024,
        "task_ttl_hours": TASK_TTL_HOURS,
    }


# --- Saucedo Multi-Tier Memory (short/medium/long) ---
# Факты с тирами: short — рабочая память, medium — консолидируемая,
# long — долгосрочная. Продвижение только вверх (consolidate/promote).

def _tier_index(tier: str) -> int:
    try:
        return MEMORY_TIERS.index(tier)
    except ValueError:
        return -1


def store_memory(key: str, value: str, tier: str = "short") -> bool:
    """Сохранить факт в память. True если ключ НОВЫЙ.

    tier: short/medium/long. Повторный store обновляет value и tier,
    сохраняя access-историю. Невалидные входы → False без записи.
    """
    if not isinstance(key, str) or not key.strip():
        return False
    if not isinstance(value, str):
        return False
    if _tier_index(tier) < 0:
        return False
    _ensure_db()
    key = key.strip()
    now = time.time()
    with _get_conn() as conn:
        exists = conn.execute(
            "SELECT id, value, tier FROM memory_items WHERE key = ?", (key,)
        ).fetchone()
        new = exists is None
        if new:
            conn.execute(
                "INSERT INTO memory_items (key, value, tier, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, value, tier, now, now),
            )
        else:
            # Temporal supersession: value изменился — старый факт НЕ теряем,
            # фиксируем superseded-версию (provenance) до обновления.
            if exists["value"] != value:
                conn.execute(
                    "INSERT INTO memory_history (key, old_value, old_tier,"
                    " new_value, superseded_at) VALUES (?, ?, ?, ?, ?)",
                    (key, exists["value"], exists["tier"], value, now),
                )
            conn.execute(
                "UPDATE memory_items SET value = ?, tier = ?, updated_at = ?"
                " WHERE key = ?",
                (value, tier, now, key),
            )
        conn.commit()
    if new:
        append_audit("memory_store", {"key": key[:80], "tier": tier})
    return new


def get_memory_history(key: str = None, limit: int = 50) -> list[dict]:
    """История перезаписей факта (superseded версии), newest-first.

    key=None — все ключи. Пусто → []. Append-only: записи не редактируются
    и переживают удаление факта из memory_items.
    """
    if key is not None and (not isinstance(key, str) or not key.strip()):
        return []
    if not isinstance(limit, int) or limit < 1:
        limit = 50
    _ensure_db()
    sql = ("SELECT key, old_value, old_tier, new_value, superseded_at"
           " FROM memory_history")
    args = ()
    if key is not None:
        sql += " WHERE key = ?"
        args = (key.strip(),)
    sql += " ORDER BY id DESC LIMIT ?"
    with _get_conn() as conn:
        rows = conn.execute(sql, args + (limit,)).fetchall()
    return [dict(r) for r in rows]


def memory_history_stats() -> dict:
    """Статистика superseded-истории: {'total': n, 'keys': n}."""
    _ensure_db()
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM memory_history").fetchone()["c"]
        keys = conn.execute(
            "SELECT COUNT(DISTINCT key) as c FROM memory_history"
        ).fetchone()["c"]
    return {"total": total, "keys": keys}


def get_memory(key: str) -> dict | None:
    """Прочитать факт: инкремент access_count, обновление last_access.

    None — ключ не найден или невалиден.
    """
    if not isinstance(key, str) or not key.strip():
        return None
    _ensure_db()
    now = time.time()
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE memory_items SET access_count = access_count + 1,"
            " last_access = ? WHERE key = ?",
            (now, key.strip()),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT key, value, tier, created_at, updated_at,"
            " access_count, last_access FROM memory_items WHERE key = ?",
            (key.strip(),),
        ).fetchone()
        conn.commit()
    return dict(row)


def promote_memory(key: str, to_tier: str) -> bool:
    """Повысить тир факта. Только вверх: short→medium→long.

    False: ключ не найден, невалидный tier, либо тир не выше текущего.
    """
    if not isinstance(key, str) or not key.strip():
        return False
    target = _tier_index(to_tier)
    if target < 0:
        return False
    _ensure_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT tier FROM memory_items WHERE key = ?", (key.strip(),)
        ).fetchone()
        if not row or _tier_index(row["tier"]) >= target:
            return False
        conn.execute(
            "UPDATE memory_items SET tier = ?, updated_at = ? WHERE key = ?",
            (to_tier, time.time(), key.strip()),
        )
        conn.commit()
    append_audit("memory_promote", {"key": key.strip()[:80], "to_tier": to_tier})
    return True


def consolidate_memory(short_ttl_hours: int = 24,
                       promote_accesses: int = 3,
                       medium_ttl_days: int = 7,
                       long_accesses: int = 10) -> dict:
    """Консолидация тиров (Saucedo).

    - short→medium: updated_at старше short_ttl_hours ИЛИ access_count >= promote_accesses
    - medium→long:  updated_at старше medium_ttl_days ИЛИ access_count >= long_accesses

    Возвращает {'short_to_medium': n, 'medium_to_long': n}.
    """
    _ensure_db()
    now = time.time()
    short_cutoff = now - short_ttl_hours * 3600
    med_cutoff = now - medium_ttl_days * 86400
    with _get_conn() as conn:
        s2m = conn.execute(
            "UPDATE memory_items SET tier = 'medium', updated_at = ?"
            " WHERE tier = 'short' AND (updated_at < ? OR access_count >= ?)",
            (now, short_cutoff, promote_accesses),
        ).rowcount
        m2l = conn.execute(
            "UPDATE memory_items SET tier = 'long', updated_at = ?"
            " WHERE tier = 'medium' AND (updated_at < ? OR access_count >= ?)",
            (now, med_cutoff, long_accesses),
        ).rowcount
        conn.commit()
    if s2m or m2l:
        append_audit("memory_consolidate",
                     {"short_to_medium": s2m, "medium_to_long": m2l})
    return {"short_to_medium": s2m, "medium_to_long": m2l}


def memory_stats() -> dict:
    """Счётчики по тирам: {'short': n, 'medium': n, 'long': n}."""
    _ensure_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT tier, COUNT(*) as c FROM memory_items GROUP BY tier"
        ).fetchall()
    counts = {t: 0 for t in MEMORY_TIERS}
    for r in rows:
        counts[r["tier"]] = r["c"]
    return counts


def list_memory(tier: str = None) -> list[dict]:
    """Список фактов (свежие первыми). tier=None — все; невалидный tier → []."""
    _ensure_db()
    if tier is not None and _tier_index(tier) < 0:
        return []
    with _get_conn() as conn:
        if tier is None:
            rows = conn.execute(
                "SELECT key, value, tier, updated_at, access_count"
                " FROM memory_items ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, value, tier, updated_at, access_count"
                " FROM memory_items WHERE tier = ? ORDER BY updated_at DESC",
                (tier,),
            ).fetchall()
    return [dict(r) for r in rows]


def retain_memory(max_long: int = 200, long_ttl_days: int = 30,
                  min_long_accesses: int = 5,
                  medium_ttl_days: int = 7,
                  min_medium_accesses: int = 2) -> dict:
    """Ретеншн long-тира + decay medium-тира (Saucedo).

    - stale (long): long с (last_access=0 ИЛИ last_access < now - long_ttl_days)
      И access_count < min_long_accesses → удаляются. Оба условия (AND):
      старый, но часто читаемый факт — ценный, остаётся.
    - cap (long): если long осталось > max_long → удаляются самые старые по
      last_access (затем по id — детерминизм), пока не останется max_long.
    - decay (medium): medium с (last_access=0 ИЛИ last_access < now -
      medium_ttl_days) И access_count < min_medium_accesses → ПОНИЖАЮТСЯ до
      short (факт не теряется, но теряет консолидированный статус; updated_at
      сбрасывается — повторная консолидация только после свежего доступа).
      Без decay нечитаемые medium по возрасту ПРОМОУТИЛИСЬ бы в long
      (consolidate: medium→long при updated_at < medium_ttl_days) — зомби в long.

    Отключение: long_ttl_days<=0 или min_long_accesses<=0 → stale-чистка
    выключена; medium_ttl_days<=0 или min_medium_accesses<=0 → decay выключен;
    max_long<=0 → cap выключен.

    Возвращает {'evicted_stale': n, 'evicted_cap': n, 'demoted_medium': n,
                'long_left': n}.
    Audit 'memory_retain' при ненулевых эвикциях/демоциях.
    """
    _ensure_db()
    now = time.time()
    evicted_stale = 0
    evicted_cap = 0
    demoted_medium = 0
    with _get_conn() as conn:
        if medium_ttl_days > 0 and min_medium_accesses > 0:
            med_cutoff = now - medium_ttl_days * 86400
            demoted_medium = conn.execute(
                "UPDATE memory_items SET tier = 'short', updated_at = ?"
                " WHERE tier = 'medium'"
                " AND (last_access = 0 OR last_access < ?)"
                " AND access_count < ?",
                (now, med_cutoff, min_medium_accesses),
            ).rowcount
        if long_ttl_days > 0 and min_long_accesses > 0:
            cutoff = now - long_ttl_days * 86400
            evicted_stale = conn.execute(
                "DELETE FROM memory_items WHERE tier = 'long'"
                " AND (last_access = 0 OR last_access < ?)"
                " AND access_count < ?",
                (cutoff, min_long_accesses),
            ).rowcount
        if max_long > 0:
            over = conn.execute(
                "SELECT COUNT(*) as c FROM memory_items WHERE tier = 'long'"
            ).fetchone()["c"] - max_long
            if over > 0:
                evicted_cap = conn.execute(
                    "DELETE FROM memory_items WHERE id IN ("
                    " SELECT id FROM memory_items WHERE tier = 'long'"
                    " ORDER BY last_access ASC, id ASC LIMIT ?)",
                    (over,),
                ).rowcount
        conn.commit()
    long_left = memory_stats()["long"]
    if evicted_stale or evicted_cap or demoted_medium:
        append_audit("memory_retain",
                     {"evicted_stale": evicted_stale, "evicted_cap": evicted_cap,
                      "demoted_medium": demoted_medium})
    return {"evicted_stale": evicted_stale, "evicted_cap": evicted_cap,
            "demoted_medium": demoted_medium, "long_left": long_left}


def vacuum():
    """Оптимизировать БД."""
    _ensure_db()
    with _get_conn() as conn:
        conn.execute("VACUUM")


def get_report() -> str:
    """Человекочитаемый отчёт."""
    stats = get_stats()
    ctx = load_context()
    history = get_session_history(24)

    lines = ["🗄️ Context Store (SQLite):"]
    lines.append(f"  📊 Сессий всего: {stats['total_sessions']}")
    lines.append(f"  📋 Активных задач: {stats['active_tasks']}")
    lines.append(f"  📸 Снапшотов: {stats['snapshots']}")
    lines.append(f"  💾 Размер БД: {stats['db_size_kb']} KB")
    lines.append(f"  ⏰ TTL задач: {stats['task_ttl_hours']}ч")
    mem = memory_stats()
    lines.append(f"  🧠 Память: short {mem['short']} / medium {mem['medium']} / long {mem['long']}")
    mh = memory_history_stats()
    if mh["total"]:
        lines.append(f"  🕘 Superseded версий: {mh['total']} ({mh['keys']} ключей)")

    if ctx:
        lines.append(f"\n  🧠 Последняя сессия:")
        lines.append(f"    Фаза: {ctx.get('session_phase', '?')}")
        lines.append(f"    Задача: {ctx.get('last_task', '?')[:60]}")
        pending = ctx.get("pending_tasks", [])
        if pending:
            lines.append(f"    Ожидают ({len(pending)}):")
            for t in pending[:5]:
                lines.append(f"      • {t[:70]}")

    if history:
        lines.append(f"\n  📜 Сессии за 24ч ({len(history)}):")
        for h in history[:5]:
            ts = time.strftime("%H:%M", time.localtime(h["timestamp"]))
            task = (h.get("last_task") or "?")[:50]
            phase = h.get("session_phase", "?")
            lines.append(f"    [{ts}] [{phase}] {task}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "save":
            data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
            sid = save_context(data)
            print(f"Saved session #{sid}")
        elif cmd == "load":
            sid = int(sys.argv[2]) if len(sys.argv) > 2 else None
            ctx = load_context(sid)
            print(json.dumps(ctx, indent=2, ensure_ascii=False))
        elif cmd == "add-task":
            task = " ".join(sys.argv[2:])
            if task:
                added = add_pending_task(task)
                print(f"{'Added' if added else 'Already exists'}: {task}")
        elif cmd == "rm-task":
            task = " ".join(sys.argv[2:])
            if task:
                removed = remove_pending_task(task)
                print(f"{'Removed' if removed else 'Not found'}: {task}")
        elif cmd == "age-out":
            n = age_out_tasks()
            print(f"Aged out {n} tasks")
        elif cmd == "stats":
            print(json.dumps(get_stats(), indent=2))
        elif cmd == "vacuum":
            vacuum()
            print("VACUUM done")
        elif cmd == "prune":
            res = prune_old()
            print(f"Pruned: sessions={res['sessions']}, snapshots={res['snapshots']}")
        elif cmd == "history":
            hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
            for h in get_session_history(hours):
                ts = time.strftime("%d.%m %H:%M", time.localtime(h["timestamp"]))
                print(f"[{ts}] [{h['session_phase']}] {h['last_task'][:60]}")
        elif cmd == "audit":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
            for e in get_audit(limit):
                ts = time.strftime("%d.%m %H:%M", time.localtime(e["ts"]))
                p = json.dumps(e["payload"], ensure_ascii=False)[:80]
                print(f"[{ts}] #{e['id']} {e['op']} {p}")
        elif cmd == "audit-rebuild":
            s = rebuild_summary()
            print(f"Rebuilt: total={s['total_events']}, "
                  f"op_counts={json.dumps(s['op_counts'], ensure_ascii=False)}")
        elif cmd == "audit-check":
            r = audit_integrity()
            print(f"ok={r['ok']} stale={r['stale']} "
                  f"log={r['log_events']} summary={r['summary_events']}")
            for i in r["issues"]:
                print(f"  ISSUE: {i}")
        elif cmd == "mem-store":
            if len(sys.argv) < 4:
                print("usage: mem-store <key> <value> [tier]")
            else:
                tier = sys.argv[4] if len(sys.argv) > 4 else "short"
                ok = store_memory(sys.argv[2], sys.argv[3], tier)
                print(f"{'stored' if ok else 'updated'}: {sys.argv[2]} ({tier})")
        elif cmd == "mem-get":
            if len(sys.argv) > 2:
                m = get_memory(sys.argv[2])
                if m:
                    print(f"{m['key']}: {m['value']} [{m['tier']}] "
                          f"access={m['access_count']}")
                else:
                    print("not found")
            else:
                print("usage: mem-get <key>")
        elif cmd == "mem-promote":
            if len(sys.argv) < 4:
                print("usage: mem-promote <key> <tier>")
            else:
                ok = promote_memory(sys.argv[2], sys.argv[3])
                print(f"{'promoted' if ok else 'not promoted'}: "
                      f"{sys.argv[2]} → {sys.argv[3]}")
        elif cmd == "mem-consolidate":
            res = consolidate_memory()
            print(f"consolidated: short→medium={res['short_to_medium']}, "
                  f"medium→long={res['medium_to_long']}")
        elif cmd == "mem-list":
            tier = sys.argv[2] if len(sys.argv) > 2 else None
            for m in list_memory(tier):
                print(f"[{m['tier']}] {m['key']}: {m['value'][:50]} "
                      f"access={m['access_count']}")
        elif cmd == "mem-stats":
            print(json.dumps(memory_stats(), indent=2))
        elif cmd == "mem-retain":
            max_long = int(sys.argv[2]) if len(sys.argv) > 2 else 200
            ttl = int(sys.argv[3]) if len(sys.argv) > 3 else 30
            min_acc = int(sys.argv[4]) if len(sys.argv) > 4 else 5
            res = retain_memory(max_long, ttl, min_acc)
            print(f"retained: stale={res['evicted_stale']}, "
                  f"cap={res['evicted_cap']}, long_left={res['long_left']}")
        elif cmd == "mem-maintain":
            max_long = int(sys.argv[2]) if len(sys.argv) > 2 else 200
            ttl = int(sys.argv[3]) if len(sys.argv) > 3 else 30
            min_acc = int(sys.argv[4]) if len(sys.argv) > 4 else 5
            med_ttl = int(sys.argv[5]) if len(sys.argv) > 5 else 7
            med_min_acc = int(sys.argv[6]) if len(sys.argv) > 6 else 2
            retain = retain_memory(max_long, ttl, min_acc, med_ttl, med_min_acc)
            cons = consolidate_memory()
            print(json.dumps({"retain": retain, "consolidate": cons},
                             ensure_ascii=False))
        else:
            print(get_report())
    else:
        print(get_report())
