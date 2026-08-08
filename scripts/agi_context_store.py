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

DB_PATH = Path(os.environ.get("AGI_CONTEXT_STORE_DB", "/root/.hermes/data/context_store.db"))
TASK_TTL_HOURS = 48  # авто-удаление старых задач
MAX_SESSIONS = 200   # retention: сколько последних сессий хранить
SNAPSHOT_TTL_DAYS = 7  # retention: снапшоты старше — на удаление


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_hash ON pending_tasks(task_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON pending_tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(timestamp)")
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
        return cursor.rowcount > 0


def remove_pending_task(task: str) -> bool:
    """Удалить задачу (по хешу)."""
    _ensure_db()
    th = _task_hash(task)
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM pending_tasks WHERE task_hash = ?", (th,))
        conn.commit()
        return cursor.rowcount > 0


def age_out_tasks() -> int:
    """Удалить задачи старше TTL. Возвращает количество удалённых."""
    _ensure_db()
    cutoff = time.time() - TASK_TTL_HOURS * 3600
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM pending_tasks WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount


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
        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    return {
        "total_sessions": total_sessions,
        "active_tasks": active_tasks,
        "snapshots": snapshots,
        "db_size_kb": db_size // 1024,
        "task_ttl_hours": TASK_TTL_HOURS,
    }


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
        else:
            print(get_report())
    else:
        print(get_report())
