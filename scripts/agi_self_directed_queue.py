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
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone

# Пути данных — env-переопределяемые (песочница/контейнеры: /root/.hermes
# не пишется; HERMES_HOME задаёт базу, AGI_*_FILE — точные файлы).
HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
BRIDGE_FILE = Path(os.environ.get("AGI_BRIDGE_FILE", os.path.join(HERMES_HOME, "session/bridge.json")))
PATTERNS_FILE = Path(os.environ.get("AGI_PATTERNS_FILE", os.path.join(HERMES_HOME, "data/error_patterns.json")))
KNOWLEDGE_FILE = Path(os.environ.get("AGI_KNOWLEDGE_FILE", os.path.join(HERMES_HOME, "data/curious_knowledge.json")))
QUEUE_FILE = Path(os.environ.get("AGI_QUEUE_FILE", os.path.join(HERMES_HOME, "data/task_queue.json")))

# SQLite-first: контекст через agi_session_bridge (канонический вид),
# JSON bridge.json — только fallback
_USE_BRIDGE = False
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from agi_session_bridge import load_context as _bridge_load
    _USE_BRIDGE = True
except ImportError:
    pass


def _load_bridge_context() -> dict:
    """Контекст сессии: SQLite-first через agi_session_bridge, JSON fallback."""
    if _USE_BRIDGE:
        try:
            ctx = _bridge_load()
            if ctx:
                return ctx
        except Exception:
            pass
    if BRIDGE_FILE.exists():
        try:
            return json.loads(BRIDGE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}

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
RESEARCH_STALE_HOURS = 24  # находка старше N часов → directed re-research задача
RESEARCH_REPEAT_HOURS = 12  # тема, исследованная < N часов назад, НЕ кандидат в knowledge_gap

# Скрипты-исполнители — AGI_SCRIPTS_DIR переопределяет каталог (в песочнице
# /root/.hermes/scripts недоступен; репо-каталог задаётся через env).
AGI_SCRIPTS_DIR = os.environ.get("AGI_SCRIPTS_DIR", os.path.join(HERMES_HOME, "scripts"))

# Маппинг: подстрока задачи → команда исполнения (автономный runner)
TASK_ACTIONS = [
    (("proactive scan", "health check"),
     ["python3", os.path.join(AGI_SCRIPTS_DIR, "proactive_scan.py")]),
    (("self_improve", "self-improvement"),
     ["python3", os.path.join(AGI_SCRIPTS_DIR, "self_improve.py")]),
    (("curious agent", "research cycle"),
     ["python3", os.path.join(AGI_SCRIPTS_DIR, "agi_curious_agent.py")]),
    (("pattern",),
     ["python3", os.path.join(AGI_SCRIPTS_DIR, "agi_error_pattern_learner.py"), "report"]),
]


def _ensure_dirs():
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _pick_gap_topic(dated: list[dict], now: float,
                    repeat_hours: float | None = None) -> str | None:
    """Выбрать тему для knowledge_gap из находок с валидным timestamp.

    Цикл 23: темы, исследованные < RESEARCH_REPEAT_HOURS назад, исключаются —
    у них свежие findings (directed re-research ЗАМЕНЯЕТ находку, но при
    дублях старая остаётся и раньше побеждала как «самая старая»). Последнее
    исследование темы = max timestamp её находок. Среди кандидатов — тема с
    самой старой находкой (как в цикле 20). Все темы свежие → None
    (generic-задача, консервативно — не долбим свежее).
    repeat_hours <= 0 / не число → фильтр выключен (старое поведение).
    """
    if repeat_hours is None:
        repeat_hours = RESEARCH_REPEAT_HOURS
    if not isinstance(repeat_hours, (int, float)) or repeat_hours <= 0:
        repeat_hours = 0
    newest: dict[str, float] = {}
    oldest: dict[str, float] = {}
    for f in dated:
        topic = f["topic"]
        ts = f["timestamp"]
        if topic not in newest or ts > newest[topic]:
            newest[topic] = ts
        if topic not in oldest or ts < oldest[topic]:
            oldest[topic] = ts
    candidates = [t for t in newest if (now - newest[t]) >= repeat_hours * 3600]
    if not candidates:
        return None
    return min(candidates, key=lambda t: oldest[t])


def load_state() -> dict:
    """Собрать состояние из всех источников."""
    state = {"pending": [], "risks": [], "knowledge_gaps": [], "errors_active": []}

    # Задачи из bridge (SQLite-first)
    bridge = _load_bridge_context()
    if bridge:
        state["pending"] = bridge.get("pending_tasks", [])
        state["last_task"] = bridge.get("last_task", "")
        state["last_error"] = bridge.get("last_error", "")

    # Риски из error pattern learner — trend-aware (персист из predict_risks)
    if PATTERNS_FILE.exists():
        try:
            patterns = json.loads(PATTERNS_FILE.read_text())
            risks = patterns.get("risks", [])
            if risks:
                # Новая схема: только HIGH риски (falling→low уже отфильтрован
                # learner'ом) — не плодим задачи для паттернов, что пропадают.
                for r in risks:
                    if r.get("risk") == "high" and r.get("pattern"):
                        state["risks"].append({
                            "pattern": r["pattern"],
                            "count": patterns.get("streaks", {}).get(r["pattern"], 3),
                            "trend": r.get("trend", "stable"),
                            "priority": 100,
                        })
            else:
                # Fallback: старый файл без risks — наивный streak-логик
                for name, count in patterns.get("streaks", {}).items():
                    if count >= 3:
                        state["risks"].append({
                            "pattern": name,
                            "count": count,
                            "trend": "unknown",
                            "priority": min(count * 30, 100),
                        })
        except Exception:
            pass

    # Пробелы в знаниях
    if KNOWLEDGE_FILE.exists():
        try:
            knowledge = json.loads(KNOWLEDGE_FILE.read_text())
            now = time.time()
            # Directed re-research: находки старше RESEARCH_STALE_HOURS —
            # из них планировщик делает конкретные задачи (а не один generic).
            stale = []
            for f in knowledge.get("findings", []):
                topic = f.get("topic")
                fts = f.get("timestamp", 0)
                if topic and fts and (now - fts) > RESEARCH_STALE_HOURS * 3600:
                    stale.append({"topic": topic, "age_hours": (now - fts) / 3600})
            stale.sort(key=lambda s: s["age_hours"], reverse=True)
            state["stale_topics"] = stale[:3]
            # Темы, которые давно не исследовались (fallback: generic задача).
            # Цикл 20: gap-задача несёт КОНКРЕТНУЮ тему — самую старую находку
            # (кандидат на re-research). Без валидного timestamp тему не
            # угадываем (консервативно, как в get_stale_topics).
            if knowledge.get("last_search", 0):
                hours_since = (now - knowledge["last_search"]) / 3600
                if hours_since > 6:
                    dated = [
                        f for f in knowledge.get("findings", [])
                        if isinstance(f, dict) and f.get("topic")
                        and isinstance(f.get("timestamp"), (int, float))
                    ]
                    # Цикл 23: темы, исследованные < RESEARCH_REPEAT_HOURS
                    # назад, исключаются (свежие findings = недавний
                    # re-research; старая находка темы ≠ старая тема).
                    # Все свежие → None → generic-задача без темы.
                    gap_topic = _pick_gap_topic(dated, now)
                    state["knowledge_gaps"].append({
                        "reason": f"Нет исследований {hours_since:.0f} часов",
                        "priority": min(int(hours_since * 5), 50),
                        "topic": gap_topic,
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

    # Кулдаун на уровне очереди: задачи, исполнявшиеся (run_next) в
    # последние DEFAULT_COOLDOWN часов, не пере-добавляем. Без этого
    # pending из bridge и stale-темы возвращались при каждом
    # build_queue() (циклы идут каждые 30-60 мин) — повторы плодили
    # дубли в history и спам-прогоны одной и той же задачи.
    history = load_history()
    now = time.time()
    recent_tasks = {
        h["task"] for h in history
        if now - h.get("ts", 0) < DEFAULT_COOLDOWN
    }

    # 1. Задачи из bridge — с кулдауном: pending может висеть в bridge
    # между циклами (его удаляет только mark_completed/run_next), без
    # кулдауна каждая сборка очереди возвращала его заново.
    for task in state.get("pending", []):
        if task in recent_tasks:
            continue
        cat, weight = classify_task(task)
        queue.append({
            "task": task,
            "category": cat,
            "priority": weight,
            "source": "pending",
        })

    # 2. Задачи на основе рисков — только HIGH, с кулдауном: если задача
    # уже исполнялась (run_next) в последние DEFAULT_COOLDOWN часов, не
    # пере-добавляем её в очередь — иначе каждый build_queue плодит дубли.
    for risk in state.get("risks", []):
        task_text = (
            f"Investigate and fix pattern: {risk['pattern']} "
            f"(trend: {risk.get('trend', '?')})"
        )
        if task_text in recent_tasks:
            continue
        queue.append({
            "task": task_text,
            "category": "fix",
            "priority": risk.get("priority", 80),
            "source": "risk",
        })

    # 3. Directed re-research: устаревшие темы из knowledge findings —
    # конкретная задача на тему (а не один generic "research cycle").
    # С кулдауном: если research по теме уже исполнялся (успех/фейл/
    # timeout), не пере-запускаем раньше DEFAULT_COOLDOWN — иначе
    # упавший поиск ре-квеился каждый цикл и долбил одну тему.
    for st in state.get("stale_topics", []):
        task_text = f"Run curious agent research cycle for topic: {st['topic']}"
        if task_text in recent_tasks:
            continue
        queue.append({
            "task": task_text,
            "category": "research",
            "priority": min(30 + int(st["age_hours"] / 12), 55),
            "source": "stale_topic",
        })

    # 4. Исследовательские задачи (generic fallback, если stale тем нет).
    # Цикл 20: gap-задача несёт КОНКРЕТНУЮ тему (самую старую находку) —
    # run_next пробрасывает "for topic: X" в curious_agent topic-режим;
    # без темы (пустая база) остаётся generic-цикл.
    if not state.get("stale_topics"):
        for gap in state.get("knowledge_gaps", []):
            topic = gap.get("topic")
            task_text = (
                f"Run curious agent research cycle for topic: {topic}"
                if topic else "Run curious agent research cycle"
            )
            queue.append({
                "task": task_text,
                "category": "research",
                "priority": gap.get("priority", 30),
                "source": "knowledge_gap",
            })

    # 5. Дефолтные задачи если очередь пуста (с кулдауном 6ч)
    last_runs = {h["task"]: h["ts"] for h in history}
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

    # Дедупликация по тексту задачи (оставить максимальный приоритет) —
    # pending из bridge может содержать дубли, и stale-темы не должны
    # дублировать generic-задачу из knowledge_gaps.
    best: dict[str, dict] = {}
    for item in queue:
        t = item["task"]
        if t not in best or item["priority"] > best[t]["priority"]:
            best[t] = item
    queue = list(best.values())

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

    # Directed-темы: пробрасываем "for topic: X" из текста задачи в
    # curious_agent (режим topic). Без этого задача исполняется как
    # generic-цикл и конкретная stale-тема из текста теряется.
    if cmd is not None:
        m = re.search(r"for topic:\s*(.+)", task_text, re.IGNORECASE)
        if m:
            cmd = list(cmd) + ["topic", m.group(1).strip()]

    if cmd is None:
        # Нет скрипта для исполнения — просто фиксируем пропуск
        result = {"task": task_text, "status": "skipped", "reason": "no action mapping"}
    elif cmd[0] == "python3" and len(cmd) > 1 and not os.path.isfile(cmd[1]):
        # Скрипт отсутствует — честный error ДО запуска (иначе python3 вернул
        # бы exit 2 и задача выглядела бы как "failed", хотя проблема в конфиге)
        result = {"task": task_text, "status": "error",
                  "reason": f"script not found: {cmd[1]}"}
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


def enqueue_topic(topic: str, priority: int = 45,
                  source: str = "stale_prune") -> bool:
    """Напрямую добавить directed re-research задачу в очередь.

    Используется связкой prune → queue (agi_curious_agent): ценные
    stale-темы после prune попадают в очередь как re-research задачи,
    минуя build_queue. Правила:
    - пустая тема → False (без записи)
    - dedup: тема уже в очереди → False
    - кулдаун: задача исполнялась за DEFAULT_COOLDOWN часов → False
    Возвращает True, если задача реально добавлена.
    """
    if not isinstance(topic, str) or not topic.strip():
        return False
    task_text = f"Run curious agent research cycle for topic: {topic.strip()}"
    queue = load_queue()
    if any(t.get("task") == task_text for t in queue):
        return False
    now = time.time()
    history = load_history()
    if any(h.get("task") == task_text and now - h.get("ts", 0) < DEFAULT_COOLDOWN
           for h in history):
        return False
    queue.append({
        "task": task_text,
        "category": "research",
        "priority": priority,
        "source": source,
    })
    save_queue(queue)
    return True


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
