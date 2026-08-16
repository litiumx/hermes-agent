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

# Пути данных — env-переопределяемые (песочница/контейнеры не пишут
# /root/.hermes; HERMES_HOME задаёт базу, AGI_SESSION_DIR — точный путь).
HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
SESSION_DIR = Path(os.environ.get("AGI_SESSION_DIR",
                                  os.path.join(HERMES_HOME, "session")))
BRIDGE_FILE = SESSION_DIR / "bridge.json"
HISTORY_DIR = SESSION_DIR / "history"
MAX_ARCHIVE = 10
MAX_CONTEXT_SIZE_KB = 64
_MAX_STRING_LEN = 2000  # обрезка длинных строк в снапшотах (traceback'и и т.п.)
_TASK_CREATED_KEY = "_task_created"  # sidecar: task -> unix ts (JSON-бэкенд)
JSON_TASK_TTL_HOURS = 48            # паритет с agi_context_store.TASK_TTL_HOURS

# --- SQLite backend (primary) ---
import importlib

_STORE = None
_USE_SQLITE = False
try:
    sys.path.insert(0, str(Path(__file__).parent))  # для импорта из других скриптов
    _STORE = importlib.import_module("agi_context_store")
    _USE_SQLITE = True
except ImportError:
    _STORE = None


def _store_call(fn_name: str, *args, **kwargs):
    """Вызвать функцию SQLite-бэкенда. Падает только если бэкенд недоступен —
    все вызывающие места предварительно проверяют _USE_SQLITE."""
    if _STORE is None:
        raise RuntimeError("SQLite backend unavailable")
    return getattr(_STORE, fn_name)(*args, **kwargs)


def _sql_history_safe(hours: int) -> list:
    """Обёртка: история из SQLite или [] — без падения."""
    if not _USE_SQLITE:
        return []
    return _store_call("get_session_history", hours)


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
            _store_call("save_context", context)
            diff = _compute_diff(prev, _canonicalize(context))
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


_DIFF_IGNORE = {"timestamp", "_task_created"}  # регенерируется при каждом сохранении — не информативен в диффе


def load_context() -> dict:
    """Загрузить контекст предыдущей сессии. SQLite-first, JSON fallback.

    Возвращает КАНОНИЧЕСКИЙ контекст (через _canonicalize) — оба бэкенда
    отдают одинаковый набор ключей, чтобы дифф не шумел на id/swarm_size.
    """
    _ensure_dirs()

    if _USE_SQLITE:
        try:
            ctx = _store_call("load_context")
            if ctx:
                return _canonicalize(ctx)
        except Exception:
            pass

    # JSON fallback
    if BRIDGE_FILE.exists():
        try:
            return _canonicalize(json.loads(BRIDGE_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _canonicalize(context: dict) -> dict:
    """Единый канонический вид контекста из ЛЮБОГО источника.

    Схлопывает различия ключей между SQLite (id/swarm_size/last_known_good
    int 0|1) и JSON-путем (current_swarm_size/last_known_good_state bool):
    - swarm_size → current_swarm_size
    - last_known_good (int) → last_known_good_state (bool)
    - id — служебный, в дифф не попадает
    """
    # Sidecar: timestamps задач JSON-бэкенда (SQLite хранит created_at в БД).
    # Пруним ключи без живой задачи — удалённые не оставляют хвостов.
    _live = {t.strip().lower() for t in context.get("pending_tasks", [])
             if isinstance(t, str)}
    _created = context.get(_TASK_CREATED_KEY, {})
    if isinstance(_created, dict):
        _created = {k: v for k, v in _created.items()
                    if k.strip().lower() in _live}
    return {
        "timestamp": context.get("timestamp", 0),
        "last_task": context.get("last_task", ""),
        "active_projects": context.get("active_projects", []),
        "last_error": context.get("last_error", ""),
        "user_preferences": context.get("user_preferences", {}),
        "current_swarm_size": context.get("current_swarm_size", context.get("swarm_size", 3)),
        "pending_tasks": context.get("pending_tasks", []),
        "last_known_good_state": bool(
            context.get("last_known_good_state", context.get("last_known_good", True))
        ),
        "modified_files": context.get("modified_files", []),
        "session_phase": context.get("session_phase", "unknown"),
        "tool_call_count": context.get("tool_call_count", 0),
        _TASK_CREATED_KEY: _created,
    }


def _normalize_context(context: dict) -> dict:
    """Канонический вид + свежий timestamp (для сохранения в JSON)."""
    ctx = _canonicalize(context)
    ctx["timestamp"] = time.time()
    return ctx


def _compute_diff(prev: dict, curr: dict) -> dict:
    """Вычислить разницу между контекстами (без служебных ключей)."""
    diff = {}
    for key in set(list(prev.keys()) + list(curr.keys())):
        if key in _DIFF_IGNORE:
            continue
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


def add_task_json(task: str) -> bool:
    """Добавить pending task в JSON-бэкенд с дедупом (как SQLite-путь).

    Возвращает True если добавлено, False если такой task уже есть
    (case-insensitive, с учётом пробелов по краям). Записывает timestamp
    в sidecar _task_created — иначе age-out в JSON-режиме невозможен.
    """
    ctx = load_context()
    tasks = ctx.setdefault("pending_tasks", [])
    norm = task.strip().lower()
    if any(t.strip().lower() == norm for t in tasks):
        return False
    tasks.append(task.strip())
    created = ctx.setdefault(_TASK_CREATED_KEY, {})
    created[task.strip()] = time.time()
    save_context(ctx, snapshot=False)
    return True


def age_out_tasks_json(max_age_hours: float = JSON_TASK_TTL_HOURS) -> int:
    """Удалить задачи JSON-бэкенда старше TTL (паритет с SQLite age_out_tasks).

    Задачи БЕЗ timestamp в sidecar (legacy-данные) НЕ удаляются — возраст
    неизвестен, терять их нельзя. Возвращает количество удалённых.
    """
    ctx = load_context()
    tasks = ctx.get("pending_tasks", [])
    if not tasks:
        return 0
    created = ctx.get(_TASK_CREATED_KEY, {})
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    keep = []
    for t in tasks:
        ts = created.get(t)
        if ts is not None and isinstance(ts, (int, float)) and ts < cutoff:
            removed += 1
        else:
            keep.append(t)
    if removed:
        ctx["pending_tasks"] = keep
        save_context(ctx, snapshot=False)
    return removed


def get_stats_json() -> dict:
    """Статистика JSON-бэкенда (паритет с agi_context_store.get_stats)."""
    ctx = load_context()
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json")) if HISTORY_DIR.exists() else []
    size_kb = BRIDGE_FILE.stat().st_size // 1024 if BRIDGE_FILE.exists() else 0
    return {
        "backend": "JSON",
        "active_tasks": len(ctx.get("pending_tasks", [])),
        "snapshots": len(snapshots),
        "bridge_size_kb": size_kb,
        "task_ttl_hours": JSON_TASK_TTL_HOURS,
    }


def history_json(hours: float = 24) -> list:
    """История сессий из JSON-снапшотов (fallback-бэкенд, цикл 33).

    Паритет с SQLite-веткой get_session_history(hours):
    - фильтр по окну часов (снапшоты старше cutoff отбрасываются);
    - сортировка новые -> старые;
    - снапшот БЕЗ timestamp считается «возраст неизвестен» и ВКЛЮЧАЕТСЯ
      (та же политика, что в age_out_tasks_json — не теряем legacy-данные);
    - битые снапшоты (JSONDecodeError/OSError) пропускаются, остальные целы.
    Возвращает записи {timestamp, session_phase, last_task} (ts=0 для legacy).
    """
    if not HISTORY_DIR.exists():
        return []
    cutoff = time.time() - hours * 3600
    entries = []
    for s in sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True):
        try:
            data = json.loads(s.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ts = data.get("timestamp")
        if isinstance(ts, (int, float)) and ts < cutoff:
            continue  # старше окна
        entries.append({
            "timestamp": ts if isinstance(ts, (int, float)) else 0,
            "session_phase": data.get("session_phase", "?"),
            "last_task": data.get("last_task", "?"),
        })
    return entries


def _format_history_line(entry: dict) -> str:
    """Формат CLI history: [дд.мм чч:мм] [фаза] задача. ts=0 (legacy) -> '??.?? ??:??'."""
    if entry.get("timestamp"):
        ts = time.strftime("%d.%m %H:%M", time.localtime(entry["timestamp"]))
    else:
        ts = "??.?? ??:??"
    return f"[{ts}] [{entry.get('session_phase', '?')}] {entry.get('last_task', '?')[:60]}"


def _archive_snapshot(ctx: dict):
    """Сохранить снимок в историю, ротировать старые."""
    _ensure_dirs()
    ts = ctx.get("timestamp", time.time())
    # ms-точность в имени — иначе два снапшота в одну секунду перезаписывают друг друга
    snapshot_file = HISTORY_DIR / f"snapshot_{ts * 1000:.0f}.json"

    # Ограничиваем размер: сначала обрезаем длинные строки (traceback'и,
    # гигантские last_error), потом отбрасываем тяжёлые list/dict поля
    serialized = json.dumps(ctx, indent=2, ensure_ascii=False)
    if len(serialized) > MAX_CONTEXT_SIZE_KB * 1024:
        ctx = {
            k: (v if not isinstance(v, str) or len(v) <= _MAX_STRING_LEN
                else v[:_MAX_STRING_LEN] + "...[trunc]")
            for k, v in ctx.items()
            if not isinstance(v, (list, dict)) or len(str(v)) < 500
        }
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

    # Показываем историю (SQLite-first, JSON fallback)
    if _USE_SQLITE:
        try:
            hist = _sql_history_safe(24 * 7)
            if hist:
                lines.append(f"\n  📜 История (SQLite, {len(hist)} сессий за 7д):")
                for h in hist[:3]:
                    ts = time.strftime("%d.%m %H:%M", time.localtime(h["timestamp"]))
                    task = (h.get("last_task") or "?")[:50]
                    phase = h.get("session_phase", "?")
                    lines.append(f"    [{ts}] [{phase}] {task}")
        except Exception:
            pass
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True)[:3]
    if snapshots:
        lines.append(f"\n  📜 Снимки (JSON, {len(list(HISTORY_DIR.glob('snapshot_*.json')))} шт):")
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
    """SIGUSR1: сохранить контекст через ОСНОВНОЙ бэкенд (SQLite-first).

    Раньше писал напрямую в JSON bridge — при активном SQLite это создавало
    рассинхрон: load_context() читал SQLite, а сюда попадал JSON. Теперь
    save_context() сам выбирает бэкенд и архивирует снимок.
    """
    _ensure_dirs()
    ctx = load_context()
    ctx["session_phase"] = "interrupted"
    ctx["timestamp"] = time.time()
    save_context(ctx, snapshot=True)


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
                ok = _store_call("add_pending_task", task)
                print(f"{'Added' if ok else 'Already exists'} (SQLite): {task}")
            else:
                ok = add_task_json(task)
                print(f"{'Added' if ok else 'Already exists'} (JSON): {task}")
    elif len(sys.argv) > 1 and sys.argv[1] == "rm-task":
        task = " ".join(sys.argv[2:])
        if task:
            if _USE_SQLITE:
                ok = _store_call("remove_pending_task", task)
                print(f"{'Removed' if ok else 'Not found'} (SQLite): {task}")
            else:
                ctx = load_context()
                tasks = ctx.get("pending_tasks", [])
                before = len(tasks)
                ctx["pending_tasks"] = [t for t in tasks if t != task]
                if len(ctx["pending_tasks"]) != before:
                    save_context(ctx, snapshot=False)
                    print(f"Removed (JSON): {task}")
                else:
                    print(f"Not found: {task}")
    elif len(sys.argv) > 1 and sys.argv[1] == "age-out":
        hours = float(sys.argv[2]) if len(sys.argv) > 2 else JSON_TASK_TTL_HOURS
        if _USE_SQLITE:
            n = _store_call("age_out_tasks")
            print(f"Aged out {n} tasks (SQLite)")
        else:
            n = age_out_tasks_json(max_age_hours=hours)
            print(f"Aged out {n} tasks (JSON)")
    elif len(sys.argv) > 1 and sys.argv[1] == "history":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        if _USE_SQLITE:
            for h in _sql_history_safe(hours):
                print(_format_history_line(h))
        else:
            for h in history_json(hours):
                print(_format_history_line(h))
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        if _USE_SQLITE:
            stats = _store_call("get_stats")
            print(f"Backend: SQLite | Sessions: {stats['total_sessions']} | Tasks: {stats['active_tasks']} | DB: {stats['db_size_kb']}KB")
        else:
            stats = get_stats_json()
            print(f"Backend: JSON | Tasks: {stats['active_tasks']} | Snapshots: {stats['snapshots']} | Bridge: {stats['bridge_size_kb']}KB")
    elif len(sys.argv) > 1 and sys.argv[1] == "backend":
        print(f"Current backend: {'SQLite' if _USE_SQLITE else 'JSON (fallback)'}")
    else:
        print(get_session_summary())
