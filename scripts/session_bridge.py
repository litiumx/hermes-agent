#!/usr/bin/env python3
"""session_bridge.py — сохраняет ключевой контекст между рестартами.
Читается при старте из proactive_scan. Пишется при каждом значимом действии."""
import json, os, time
from pathlib import Path

SESSION_DIR = Path("/root/.hermes/session")
SESSION_FILE = SESSION_DIR / "bridge.json"
LAST_SESSION_FILE = SESSION_DIR / "last_session.json"

SESSION_DIR.mkdir(exist_ok=True)

def save_context(context: dict):
    """Сохранить текущий контекст. Вызывать после каждого крупного действия."""
    ctx = {
        "timestamp": time.time(),
        "last_task": context.get("last_task", ""),
        "active_projects": context.get("active_projects", []),
        "last_error": context.get("last_error", ""),
        "user_preferences": context.get("user_preferences", {}),
        "current_swarm_size": context.get("swarm_size", 3),
        "pending_tasks": context.get("pending_tasks", []),
        "last_known_good_state": context.get("last_known_good", True),
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)

def load_context() -> dict:
    """Загрузить контекст предыдущей сессии."""
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {}

def archive_session():
    """Архивировать текущую сессию при завершении."""
    if SESSION_FILE.exists():
        SESSION_FILE.rename(SESSION_DIR / f"session_{int(time.time())}.json")

def get_last_session_summary() -> str:
    """Вернуть человекочитаемый саммари предыдущей сессии."""
    ctx = load_context()
    if not ctx:
        return "Нет данных предыдущей сессии."
    
    last_task = ctx.get("last_task", "неизвестно")
    projects = ctx.get("active_projects", [])
    error = ctx.get("last_error", "")
    pending = ctx.get("pending_tasks", [])
    
    lines = [
        f"🧠 Предыдущая сессия:",
        f"  Последняя задача: {last_task}",
        f"  Активные проекты: {', '.join(projects) if projects else 'нет'}",
    ]
    if error:
        lines.append(f"  ⚠️ Последняя ошибка: {error}")
    if pending:
        lines.append(f"  📋 Ожидают: {', '.join(pending[:3])}")
    
    t = ctx.get("timestamp", 0)
    if t:
        lines.append(f"  🕐 Сессия была: {time.strftime('%d.%m %H:%M', time.localtime(t))}")
    
    return "\n".join(lines)

if __name__ == "__main__":
    # Тест
    print(get_last_session_summary())
