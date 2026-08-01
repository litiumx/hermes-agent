#!/usr/bin/env python3
"""agi_session_bridge.py — улучшенное хранение контекста между рестартами.

Фичи v2 (SQLite-first):
- SQLite через agi_context_store (WAL, дедупликация, aging, атомарность)
- JSON fallback при недоступности SQLite
- Ротация: хранит последние K сессий (по умолчанию 10)
- Диффы: отслеживает что изменилось между сохранениями
- Авто-сохранение по SIGUSR1
- Интеграция с proactive_scan.py
- Человекочитаемый саммари нескольких сессий
"""
import json, os, signal, sys, time
from pathlib import Path
from typing import Optional

SESSION_DIR = Path("/root/.hermes/session")
BRIDGE_FILE = SESSION_DIR / "bridge.json"
HISTORY_DIR = SESSION_DIR / "history"
MAX_ARCHIVE = 10
MAX_CONTEXT_SIZE_KB = 64

# --- SQLite backend (primary) ---
_USE_SQLITE = False
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from agi_context_store import (
        save_context as _sql_save,
        load_context as _sql_load,
        add_pending_task as _sql_add_task,
        remove_pending_task as _sql_rm_task,
        age_out_tasks as _sql_age_out,
        get_session_history as _sql_history,
        get_stats as _sql_stats,
    )
    _USE_SQLITE = True
except ImportError:
    pass


def _ensure_dirs():
    SESSION_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)


def save_context(context: dict, snapshot: bool = True) -> str:
    """Сохранить текущий контекст. SQLite-first, JSON fallback. Возвращает diff."""
    _ensure_dirs()

    # --- SQLite primary path ---
    if _USE_SQLITE:
        try:
            prev = load_context()  # ДО записи — иначе diff всегда пуст
            _sql_save(context)
            diff = _compute_diff(prev, _normalize_context(context)) if prev else {}
            return _format_diff(diff) if diff else "no changes (SQLite)"
        except Exception as e:
            pass  # fall through to JSON

    # --- JSON fallback ---
    prev = load_context() if BRIDGE_FILE.exists() else {}
    ctx = _normalize_context(context)

    diff = _compute_diff(prev, ctx)

    with open(BRIDGE_FILE, "w") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)

    if snapshot and ctx.get("session_phase") in ("complete", "interrupted", "error"):
        _archive_snapshot(ctx)

    return _format_diff(diff) if diff else "no changes (JSON)"


def load_context() -> dict:
    """Загрузить контекст предыдущей сессии. SQLite-first, JSON fallback."""
    _ensure_dirs()

    if _USE_SQLITE:
        try:
            ctx = _sql_load()
            if ctx:
                return ctx
        except Exception:
            pass

    # JSON fallback
    if BRIDGE_FILE.exists():
        try:
            return json.loads(BRIDGE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _normalize_context(context: dict) -> dict:
    """Привести контекст к единому формату (для JSON fallback)."""
    return {
        "timestamp": time.time(),
        "last_task": context.get("last_task", ""),
        "active_projects": context.get("active_projects", []),
        "last_error": context.get("last_error", ""),
        "user_preferences": context.get("user_preferences", {}),
        "current_swarm_size": context.get("swarm_size", 3),
        "pending_tasks": context.get("pending_tasks", []),
        "last_known_good_state": context.get("last_known_good", True),
        "modified_files": context.get("modified_files", []),
        "session_phase": context.get("session_phase", "unknown"),
        "tool_call_count": context.get("tool_call_count", 0),
    }


def _compute_diff(prev: dict, curr: dict) -> dict:
    """Вычислить разницу между контекстами."""
    diff = {}
    for key in set(list(prev.keys()) + list(curr.keys())):
        pv = prev.get(key)
        cv = curr.get(key)
        if pv != cv:
            diff[key] = {"old": pv, "new": cv}
    return diff


def _format_diff(diff: dict) -> str:
    """Форматировать diff в читаемую строку."""
    parts = []
    for key, change in diff.items():
        old = str(change["old"])[:80]
        new = str(change["new"])[:80]
        parts.append(f"  {key}: {old} → {new}")
    return "\n".join(parts) if parts else "no changes"


def _archive_snapshot(ctx: dict):
    """Сохранить снимок в историю, ротировать старые."""
    _ensure_dirs()
    ts = ctx.get("timestamp", time.time())
    snapshot_file = HISTORY_DIR / f"snapshot_{ts:.0f}.json"

    # Ограничиваем размер
    serialized = json.dumps(ctx, indent=2, ensure_ascii=False)
    if len(serialized) > MAX_CONTEXT_SIZE_KB * 1024:
        ctx = {k: v for k, v in ctx.items() if not isinstance(v, (list, dict)) or len(str(v)) < 500}
        serialized = json.dumps(ctx, indent=2, ensure_ascii=False)

    snapshot_file.write_text(serialized)

    # Ротация: удаляем старые
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"))
    for old in snapshots[:-MAX_ARCHIVE]:
        try:
            old.unlink()
        except OSError:
            pass


def get_session_summary() -> str:
    """Расширенный саммари текущей + последних сессий."""
    ctx = load_context()
    if not ctx:
        return "Нет данных предыдущей сессии."

    lines = ["🧠 Состояние сессии:"]
    last_task = ctx.get("last_task", "неизвестно")
    projects = ctx.get("active_projects", [])
    error = ctx.get("last_error", "")
    pending = ctx.get("pending_tasks", [])
    phase = ctx.get("session_phase", "unknown")

    lines.append(f"  📍 Фаза: {phase}")
    lines.append(f"  📋 Задача: {last_task}")
    lines.append(f"  📂 Проекты: {', '.join(projects) if projects else 'нет'}")

    if error:
        lines.append(f"  ⚠️ Ошибка: {error}")
    if pending:
        lines.append(f"  ⏳ Ожидают: {', '.join(pending[:3])}")

    t = ctx.get("timestamp", 0)
    if t:
        lines.append(f"  🕐 Сессия: {time.strftime('%d.%m %H:%M', time.localtime(t))}")

    # Показываем историю
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True)[:3]
    if snapshots:
        lines.append(f"\n  📜 История ({len(list(HISTORY_DIR.glob('snapshot_*.json')))} снимков):")
        for s in snapshots:
            try:
                data = json.loads(s.read_text())
                ts = data.get("timestamp", 0)
                task = data.get("last_task", "?")[:50]
                phase = data.get("session_phase", "?")
                lines.append(f"    {time.strftime('%d.%m %H:%M', time.localtime(ts))} [{phase}] {task}")
            except Exception:
                pass

    # Свободное место
    stat = os.statvfs(SESSION_DIR) if SESSION_DIR.exists() else None
    if stat:
        free_mb = stat.f_bavail * stat.f_frsize / 1024 / 1024
        lines.append(f"\n  💾 Свободно: {free_mb:.0f} MB")

    return "\n".join(lines)


def _signal_handler(signum, frame):
    """SIGUSR1: сохранить контекст без вывода."""
    _ensure_dirs()
    ctx = load_context()
    ctx["session_phase"] = "interrupted"
    ctx["timestamp"] = time.time()
    with open(BRIDGE_FILE, "w") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)
    _archive_snapshot(ctx)


signal.signal(signal.SIGUSR1, _signal_handler)

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "save":
        data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        print(save_context(data))
    elif len(sys.argv) > 1 and sys.argv[1] == "summary":
        print(get_session_summary())
    elif len(sys.argv) > 1 and sys.argv[1] == "add-task":
        task = " ".join(sys.argv[2:])
        if task:
            if _USE_SQLITE:
                ok = _sql_add_task(task)
                print(f"{'Added' if ok else 'Already exists'} (SQLite): {task}")
            else:
                ctx = load_context()
                ctx.setdefault("pending_tasks", []).append(task)
                save_context(ctx, snapshot=False)
                print(f"Added (JSON): {task}")
    elif len(sys.argv) > 1 and sys.argv[1] == "rm-task":
        task = " ".join(sys.argv[2:])
        if task:
            if _USE_SQLITE:
                ok = _sql_rm_task(task)
                print(f"{'Removed' if ok else 'Not found'} (SQLite): {task}")
            else:
                ctx = load_context()
                tasks = ctx.get("pending_tasks", [])
                if task in tasks:
                    tasks.remove(task)
                    save_context(ctx, snapshot=False)
                    print(f"Removed (JSON): {task}")
                else:
                    print(f"Not found: {task}")
    elif len(sys.argv) > 1 and sys.argv[1] == "age-out":
        if _USE_SQLITE:
            n = _sql_age_out()
            print(f"Aged out {n} tasks (SQLite)")
        else:
            print("age-out requires SQLite backend")
    elif len(sys.argv) > 1 and sys.argv[1] == "history":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        if _USE_SQLITE:
            for h in _sql_history(hours):
                ts = time.strftime("%d.%m %H:%M", time.localtime(h["timestamp"]))
                print(f"[{ts}] [{h['session_phase']}] {h['last_task'][:60]}")
        else:
            snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True)
            for s in snapshots:
                try:
                    data = json.loads(s.read_text())
                    ts = time.strftime("%d.%m %H:%M", time.localtime(data.get("timestamp", 0)))
                    print(f"[{ts}] [{data.get('session_phase', '?')}] {data.get('last_task', '?')[:60]}")
                except Exception:
                    pass
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        if _USE_SQLITE:
            stats = _sql_stats()
            print(f"Backend: SQLite | Sessions: {stats['total_sessions']} | Tasks: {stats['active_tasks']} | DB: {stats['db_size_kb']}KB")
        else:
            print("Backend: JSON | stats unavailable (use 'summary')")
    elif len(sys.argv) > 1 and sys.argv[1] == "backend":
        print(f"Current backend: {'SQLite' if _USE_SQLITE else 'JSON (fallback)'}")
    else:
        print(get_session_summary())
