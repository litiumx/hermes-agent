#!/usr/bin/env python3
"""
Session Logger — логирует сессии в структурированный JSON
для последующего поиска и анализа.

Источники данных (в порядке приоритета):
1. state.db — реальный session store Hermes (~/.hermes/state.db)
2. sessions.db — собственная БД скрипта (legacy)
3. WAL JSONL (~/.hermes/sessions/*/wal.jsonl) — мёртвый формат, только fallback
"""
import json, os, sqlite3, time
from pathlib import Path

DB = os.path.expanduser("~/.hermes/sessions.db")
LOG_DIR = os.path.expanduser("~/.hermes/session-logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Реальный session store Hermes (память: "Session store=~/.hermes/state.db (WAL мёртв)")
STATE_DB_CANDIDATES = [
    os.path.expanduser("~/.hermes/state.db"),
    "/root/.hermes/state.db",
    os.path.join(os.environ.get("HERMES_HOME", "/root/.hermes"), "state.db"),
]


def find_state_db():
    """Найти реальный session store Hermes."""
    for p in STATE_DB_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def table_cols(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def search_state_db(db_path, query, limit=5):
    """Поиск по state.db (реальный session store Hermes).

    message_count в sessions — turns counter (ненадёжен),
    поэтому считаем реальное число сообщений через COUNT(*) из messages.
    """
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "sessions" not in tables:
            return []
        s_cols = table_cols(conn, "sessions")
        m_cols = table_cols(conn, "messages")
        if "id" not in s_cols:
            return []

        id_col = "id"
        title_col = "title" if "title" in s_cols else id_col
        created_col = "created_at" if "created_at" in s_cols else "started_at"
        active_col = next((c for c in ("last_active", "updated_at") if c in s_cols), created_col)

        # Счётчик сообщений: COUNT(*) из messages (если есть связь по session_id)
        if "messages" in tables and "session_id" in m_cols:
            msg_subq = "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id)"
        else:
            msg_subq = "s.message_count"

        sql = f"""
            SELECT s.{id_col}, s.{title_col}, s.{created_col}, {msg_subq}
            FROM sessions s
            ORDER BY s.{active_col} DESC
        """
        rows = conn.execute(sql).fetchall()
        # Фильтр в Python: SQLite LIKE — ASCII-only (кириллица 'Телеграм' vs 'телеграм' не матчится)
        q = (query or "").strip().lower()
        if q:
            rows = [r for r in rows if q in str(r[0]).lower() or q in str(r[1]).lower()]
        return rows[:limit]
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            created_at TEXT,
            last_active TEXT,
            messages_count INTEGER DEFAULT 0,
            tokens_used INTEGER DEFAULT 0,
            model TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            created_at TEXT,
            tokens INTEGER DEFAULT 0
        )
    """)
    # Auto-migrate old schema if columns missing
    for table, col, col_def in [
        ("sessions", "messages_count", "INTEGER DEFAULT 0"),
        ("sessions", "tokens_used", "INTEGER DEFAULT 0"),
        ("sessions", "model", "TEXT"),
        ("messages", "tool_calls", "TEXT"),
        ("messages", "tokens", "INTEGER DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


def log_session(session_id, title, messages):
    conn = init_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Upsert session
    conn.execute("""
        INSERT INTO sessions (id, title, preview, created_at, last_active, messages_count, model)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_active=excluded.last_active,
            messages_count=excluded.messages_count
    """, (session_id, title, title[:200], now, now, len(messages), "deepseek-v4-flash"))

    # Log each message
    for msg in messages:
        conn.execute("""
            INSERT INTO messages (session_id, role, content, tool_calls, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, msg.get("role"), msg.get("content", "")[:1000],
              json.dumps(msg.get("tool_calls", [])), now))

    conn.commit()
    conn.close()
    return True


def search_sessions(query, limit=5):
    # 1) Реальный session store Hermes — state.db
    state_db = find_state_db()
    if state_db:
        return search_state_db(state_db, query, limit)

    # 2) Fallback: собственная sessions.db (legacy)
    if os.path.isfile(DB):
        conn = init_db()
        cur = conn.execute("""
            SELECT id, title, created_at, messages_count
            FROM sessions
            WHERE title LIKE ? OR id LIKE ?
            ORDER BY last_active DESC LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        results = cur.fetchall()
        conn.close()
        if results:
            return results

    # 3) Мёртвый WAL — только чтобы показать, что данных в нём нет
    n = import_from_wal()
    if n:
        conn = init_db()
        cur = conn.execute("""
            SELECT id, title, created_at, messages_count
            FROM sessions
            WHERE title LIKE ? OR id LIKE ?
            ORDER BY last_active DESC LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        results = cur.fetchall()
        conn.close()
        return results
    return []


def import_from_wal(sessions_dir=None):
    """Import sessions from WAL JSONL files into SQLite DB."""
    if sessions_dir is None:
        sessions_dir = os.path.expanduser("~/.hermes/sessions")

    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return 0

    conn = init_db()
    imported = 0

    for wal_path in sorted(sessions_dir.glob("*/wal.jsonl")):
        session_id = wal_path.parent.name
        try:
            with open(wal_path, "r") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"  ⚠️ Пропущен {wal_path}: {e}")
            continue

        if not lines:
            continue

        # Extract metadata
        title = session_id
        model = "unknown"
        messages_count = 0
        tokens_used = 0
        first_ts = None
        last_ts = None

        for line in lines:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp", "")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            if entry.get("type") == "pre_call":
                model = entry.get("model", model)
                messages_count = entry.get("message_count", messages_count)
                # Extract title from first user message
                if title == session_id:
                    msgs = entry.get("messages_snapshot", [])
                    for m in msgs:
                        if m.get("role") == "user":
                            content = m.get("content", "")
                            # Strip IMPORTANT prefix for cron jobs
                            if "[IMPORTANT:" in content:
                                content = content.split("]\n\n", 1)[-1] if "]\n\n" in content else content.split("]", 1)[-1]
                            title = content.strip()[:200]
                            break

            if entry.get("type") == "post_call":
                usage = entry.get("usage", {})
                tokens_used += usage.get("total_tokens", 0)

        if first_ts is None:
            first_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if last_ts is None:
            last_ts = first_ts

        # Upsert with all fields
        conn.execute("""
            INSERT INTO sessions (id, title, preview, created_at, last_active, messages_count, tokens_used, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                preview=excluded.preview,
                last_active=excluded.last_active,
                messages_count=excluded.messages_count,
                tokens_used=excluded.tokens_used,
                model=excluded.model
        """, (session_id, title, title[:200], first_ts, last_ts,
              messages_count, tokens_used, model))

        imported += 1

    conn.commit()
    conn.close()
    return imported


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == "import":
            dir_arg = sys.argv[2] if len(sys.argv) > 2 else None
            n = import_from_wal(dir_arg)
            print(f"Импортировано сессий: {n}")
        elif action == "search":
            q = sys.argv[2] if len(sys.argv) > 2 else ""
            state_db = find_state_db()
            source = f"state.db ({state_db})" if state_db else "fallback (sessions.db/WAL)"
            results = search_sessions(q)
            if results:
                print(f"Источник: {source}")
                for s in results:
                    print(f"  {s[0][:20]} | {s[1][:60]} | {s[2][:10]} | {s[3]}msgs")
                print(f"\nВсего найдено: {len(results)}")
            else:
                print(f"Источник: {source}")
                print("Сессий не найдено. state.db недоступен в этой среде "
                      "(cron-песочница) — запустите на хосте (HOME=/root).")
    else:
        print("Session Logger — инструмент для логирования сессий")
        print("Использование:")
        print("  session_logger.py import          — импорт всех сессий из WAL в БД")
        print("  session_logger.py search <query>  — поиск по сессиям")
