#!/usr/bin/env python3
"""agi_self_directed_queue.py — автономный планировщик задач для AGI-цикла.

Читает состояние из bridge.json, error_patterns.json, curious_knowledge.json
и формирует приоритезированную очередь задач для выполнения.

Интеграция:
- agi_session_bridge.py: pending_tasks
- agi_error_pattern_learner.py: риски
- agi_curious_agent.py: находки для исследования
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

BRIDGE_FILE = Path("/root/.hermes/session/bridge.json")
PATTERNS_FILE = Path("/root/.hermes/data/error_patterns.json")
KNOWLEDGE_FILE = Path("/root/.hermes/data/curious_knowledge.json")
QUEUE_FILE = Path("/root/.hermes/data/task_queue.json")

# Категории и их веса
CATEGORY_WEIGHTS = {
    "fix": 100,      # Исправление ошибок — высший приоритет
    "security": 95,  # Безопасность
    "improve": 60,   # Улучшения
    "optimize": 50,  # Оптимизация
    "research": 30,  # Исследования
    "learn": 20,     # Обучение
}

MAX_QUEUE_SIZE = 20
TASK_TIMEOUT = 120  # секунд на выполнение одной задачи
DEFAULT_COOLDOWN = 6 * 3600  # дефолтные задачи не чаще 1 раза в 6ч

# Маппинг: подстрока задачи → команда исполнения (автономный runner)
TASK_ACTIONS = [
    (("proactive scan", "health check"),
     ["python3", "/root/.hermes/scripts/proactive_scan.py"]),
    (("self_improve", "self-improvement"),
     ["python3", "/root/.hermes/scripts/self_improve.py"]),
    (("curious agent", "research cycle"),
     ["python3", "/root/.hermes/scripts/agi_curious_agent.py"]),
    (("pattern",),
     ["python3", "/root/.hermes/scripts/agi_error_pattern_learner.py", "report"]),
]


def _ensure_dirs():
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    """Собрать состояние из всех источников."""
    state = {"pending": [], "risks": [], "knowledge_gaps": [], "errors_active": []}

    # Задачи из bridge
    if BRIDGE_FILE.exists():
        try:
            bridge = json.loads(BRIDGE_FILE.read_text())
            state["pending"] = bridge.get("pending_tasks", [])
            state["last_task"] = bridge.get("last_task", "")
            state["last_error"] = bridge.get("last_error", "")
        except Exception:
            pass

    # Риски из error pattern learner
    if PATTERNS_FILE.exists():
        try:
            patterns = json.loads(PATTERNS_FILE.read_text())
            for name, count in patterns.get("streaks", {}).items():
                if count >= 2:
                    state["risks"].append({
                        "pattern": name,
                        "count": count,
                        "priority": min(count * 30, 100),
                    })
        except Exception:
            pass

    # Пробелы в знаниях
    if KNOWLEDGE_FILE.exists():
        try:
            knowledge = json.loads(KNOWLEDGE_FILE.read_text())
            searched = set(knowledge.get("topics_searched", []))
            # Темы, которые давно не исследовались
            if knowledge.get("last_search", 0):
                hours_since = (time.time() - knowledge["last_search"]) / 3600
                if hours_since > 6:
                    state["knowledge_gaps"].append({
                        "reason": f"Нет исследований {hours_since:.0f} часов",
                        "priority": min(int(hours_since * 5), 50),
                    })
        except Exception:
            pass

    return state


def classify_task(task: str) -> tuple[str, int]:
    """Классифицировать задачу, вернуть (категория, вес)."""
    task_lower = task.lower()

    if any(w in task_lower for w in ("error", "fix", "bug", "crash", "fail", "broken")):
        return ("fix", CATEGORY_WEIGHTS["fix"])
    if any(w in task_lower for w in ("security", "vuln", "exploit", "injection", "auth")):
        return ("security", CATEGORY_WEIGHTS["security"])
    if any(w in task_lower for w in ("improve", "enhance", "refactor", "clean")):
        return ("improve", CATEGORY_WEIGHTS["improve"])
    if any(w in task_lower for w in ("optimize", "speed", "cache", "memory", "perf")):
        return ("optimize", CATEGORY_WEIGHTS["optimize"])
    if any(w in task_lower for w in ("research", "investigate", "explore", "discover")):
        return ("research", CATEGORY_WEIGHTS["research"])
    if any(w in task_lower for w in ("learn", "study", "understand")):
        return ("learn", CATEGORY_WEIGHTS["learn"])

    return ("improve", CATEGORY_WEIGHTS["improve"])  # default


def build_queue() -> list[dict]:
    """Построить приоритезированную очередь задач."""
    state = load_state()
    queue = []

    # 1. Задачи из bridge
    for task in state.get("pending", []):
        cat, weight = classify_task(task)
        queue.append({
            "task": task,
            "category": cat,
            "priority": weight,
            "source": "pending",
        })

    # 2. Задачи на основе рисков
    for risk in state.get("risks", []):
        queue.append({
            "task": f"Investigate and fix pattern: {risk['pattern']} (streak: {risk['count']})",
            "category": "fix",
            "priority": risk.get("priority", 80),
            "source": "risk",
        })

    # 3. Исследовательские задачи
    for gap in state.get("knowledge_gaps", []):
        queue.append({
            "task": f"Run curious agent research cycle",
            "category": "research",
            "priority": gap.get("priority", 30),
            "source": "knowledge_gap",
        })

    # 4. Дефолтные задачи если очередь пуста (с кулдауном 6ч)
    history = load_history()
    last_runs = {h["task"]: h["ts"] for h in history}
    now = time.time()
    default_tasks = [
        ("Run system health check and proactive scan", "improve", 40),
        ("Run self-improvement cycle (self_improve.py)", "improve", 35),
    ]
    for task, cat, prio in default_tasks:
        if now - last_runs.get(task, 0) < DEFAULT_COOLDOWN:
            continue
        queue.append({
            "task": task,
            "category": cat,
            "priority": prio,
            "source": "default",
        })

    # Сортировка по приоритету
    queue.sort(key=lambda x: x["priority"], reverse=True)
    return queue[:MAX_QUEUE_SIZE]


def save_queue(queue: list[dict]):
    """Сохранить очередь на диск (вместе с историей исполнения)."""
    _ensure_dirs()
    data = {
        "updated": time.time(),
        "updated_human": datetime.now(timezone.utc).isoformat(),
        "queue": queue,
        "history": load_history(),
    }
    QUEUE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_queue() -> list[dict]:
    """Загрузить сохранённую очередь."""
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text()).get("queue", [])
        except Exception:
            pass
    return []


def load_history() -> list[dict]:
    """Загрузить историю выполненных задач (макс 20 записей)."""
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text()).get("history", [])[-20:]
        except Exception:
            pass
    return []


def _match_action(task: str) -> list[str] | None:
    """Найти команду исполнения для задачи (по подстрокам)."""
    task_lower = task.lower()
    for keywords, cmd in TASK_ACTIONS:
        if any(k in task_lower for k in keywords):
            return cmd
    return None


def run_next() -> dict | None:
    """Исполнить следующую задачу из очереди автономно (если есть маппинг).

    Возвращает dict с результатом или None (задач нет / нет маппинга).
    """
    import subprocess

    queue = build_queue()
    if not queue:
        return {"task": None, "status": "empty"}

    task_item = queue[0]
    task_text = task_item["task"]
    cmd = _match_action(task_text)

    if cmd is None:
        # Нет скрипта для исполнения — просто фиксируем пропуск
        result = {"task": task_text, "status": "skipped", "reason": "no action mapping"}
    else:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TASK_TIMEOUT
            )
            result = {
                "task": task_text,
                "status": "done" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "output_tail": (proc.stdout or proc.stderr).strip()[-300:],
            }
        except subprocess.TimeoutExpired:
            result = {"task": task_text, "status": "timeout", "exit_code": -1}
        except Exception as exc:  # noqa: BLE001
            result = {"task": task_text, "status": "error", "reason": str(exc)}

    # История + удаление выполненной задачи из очереди
    history = load_history()
    result["ts"] = time.time()
    result["ts_human"] = datetime.now(timezone.utc).isoformat()
    history.append(result)
    _save_history(history)

    remaining = [t for t in queue if t["task"] != task_text]
    save_queue(remaining)
    return result


def _save_history(history: list[dict]):
    """Сохранить историю отдельно (чтобы не потерять при rebuild очереди)."""
    _ensure_dirs()
    data = {"updated": time.time(), "history": history[-20:]}
    QUEUE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def get_next_task() -> dict | None:
    """Получить следующую задачу (самый высокий приоритет)."""
    queue = build_queue()
    save_queue(queue)
    return queue[0] if queue else None


def mark_completed(task_description: str):
    """Отметить задачу как выполненную (удалить из очереди)."""
    queue = load_queue()
    queue = [t for t in queue if t["task"] != task_description]
    save_queue(queue)


def get_report() -> str:
    """Человекочитаемый отчёт."""
    queue = build_queue()
    save_queue(queue)

    if not queue:
        return "📋 Очередь задач пуста."

    lines = ["📋 Autonomous Task Queue:"]
    for i, item in enumerate(queue[:10]):
        emoji = {"fix": "🔴", "security": "🛡️", "improve": "🟡", "optimize": "⚡",
                 "research": "🔍", "learn": "📚"}.get(item["category"], "⚪")
        task_short = item["task"][:80]
        lines.append(f"  {i+1}. {emoji} [{item['priority']}] {task_short}")

    lines.append(f"\n  📊 Всего в очереди: {len(queue)} задач")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "next":
        next_task = get_next_task()
        if next_task:
            print(f"NEXT: [{next_task['priority']}] {next_task['task']}")
        else:
            print("NEXT: none")
    elif len(sys.argv) > 1 and sys.argv[1] == "run-next":
        result = run_next()
        if result is None or result.get("task") is None:
            print("RUN-NEXT: none")
        else:
            print(f"RUN-NEXT: [{result['status']}] {result['task']}")
            if result.get("exit_code") is not None:
                print(f"  exit_code: {result['exit_code']}")
            if result.get("reason"):
                print(f"  reason: {result['reason']}")
            if result.get("output_tail"):
                print(f"  output: {result['output_tail']}")
    elif len(sys.argv) > 1 and sys.argv[1] == "report":
        print(get_report())
    else:
        print(get_report())
