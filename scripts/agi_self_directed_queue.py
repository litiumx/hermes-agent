#!/usr/bin/env python3
"""agi_self_directed_queue.py — автономный планировщик задач для AGI-цикла.

Читает состояние из bridge.json, error_patterns.json, curious_knowledge.json
и формирует приоритезированную очередь задач для выполнения.

Интеграция:
- agi_session_bridge.py: pending_tasks
- agi_error_pattern_learner.py: риски + companion-предсказания (v4/v5)
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

# Петля обратной связи companion (цикл 26): mark_completed сообщает
# learner'у, подтвердился ли предсказанный паттерн (усиление/ослабление
# весов пар). Risk-фидбек (цикл 30): run_next сообщает, проявился ли
# паттерн риска в выводе расследования (снижение streak при опровержении).
# Lazy-импорт — модуль не обязателен, тишина при отсутствии.
_HAS_FEEDBACK = False
try:
    from agi_error_pattern_learner import feedback_companion as _feedback_companion
    from agi_error_pattern_learner import feedback_risk as _feedback_risk
    _HAS_FEEDBACK = True
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
COMPANION_MAX_TASKS = 3  # максимум companion-задач за одну сборку очереди
COMPANION_PRIORITY_CAP = 90  # потолок приоритета companion (риски=100 выше)

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


def _topic_research_times(findings) -> dict:
    """Единый источник правды по возрасту темы (цикл 27, grow point 23-26).

    Последнее исследование темы = max timestamp её находок, первое = min.
    Дубли находок НЕ плодят дубли записей — одна тема = одна запись, и
    re-research (новая находка) сдвигает «последнее исследование» вперёд,
    даже если старая находка осталась в файле. Малформы (не-dict, пустой
    topic, отсутствующий/не-числовой/<=0 timestamp) игнорируются: ts=0 —
    эпоха, не реальное время исследования.
    """
    out: dict[str, dict] = {}
    if not isinstance(findings, list):
        return out
    for f in findings:
        if not isinstance(f, dict):
            continue
        topic = f.get("topic")
        ts = f.get("timestamp")
        if not isinstance(topic, str) or not topic.strip():
            continue
        if not isinstance(ts, (int, float)) or ts <= 0:
            continue
        cur = out.get(topic)
        if cur is None:
            out[topic] = {"last": ts, "oldest": ts}
        else:
            if ts > cur["last"]:
                cur["last"] = ts
            if ts < cur["oldest"]:
                cur["oldest"] = ts
    return out


def _pick_gap_topic(dated: list[dict], now: float,
                    repeat_hours: float | None = None) -> str | None:
    """Выбрать тему для knowledge_gap из находок с валидным timestamp.

    Цикл 23: темы, исследованные < RESEARCH_REPEAT_HOURS назад, исключаются —
    у них свежие findings (directed re-research ЗАМЕНЯЕТ находку, но при
    дублях старая остаётся и раньше побеждала как «самая старая»). Последнее
    исследование темы = max timestamp её находок (общее ядро
    _topic_research_times с _pick_stale_topics — цикл 27). Среди кандидатов —
    тема с самой старой находкой (как в цикле 20). Все темы свежие → None
    (generic-задача, консервативно — не долбим свежее).
    repeat_hours <= 0 / не число → фильтр выключен (старое поведение).
    """
    if repeat_hours is None:
        repeat_hours = RESEARCH_REPEAT_HOURS
    if not isinstance(repeat_hours, (int, float)) or repeat_hours <= 0:
        repeat_hours = 0
    times = _topic_research_times(dated)
    candidates = [t for t, v in times.items()
                  if (now - v["last"]) >= repeat_hours * 3600]
    if not candidates:
        return None
    return min(candidates, key=lambda t: times[t]["oldest"])


def _pick_stale_topics(findings, now: float,
                       stale_hours: float | None = None,
                       max_topics: int = 3) -> list[dict]:
    """Directed re-research кандидаты: темы, исследованные > stale_hours назад.

    Цикл 27 (grow point 23-26): единый источник правды по возрасту с
    _pick_gap_topic — возраст темы = max timestamp её находок (последнее
    исследование), а не каждой находки по отдельности. Раньше stale-путь
    шёл построчно: дубли находок плодили дубли задач, и тема, ре-исследованная
    2ч назад, всё равно попадала в stale, если старая находка осталась.
    Теперь: одна тема = одна запись, ре-исследованная тема не stale.
    Возврат: [{"topic", "age_hours"}], сортировка по возрасту (самые старые
    первыми), cap max_topics. stale_hours <= 0 / не число → RESEARCH_STALE_HOURS
    (безопасный дефолт: не «всё stale»). Граница: строго > stale_hours.
    """
    if not isinstance(stale_hours, (int, float)) or stale_hours <= 0:
        stale_hours = RESEARCH_STALE_HOURS
    try:
        cap = max(0, int(max_topics))
    except (TypeError, ValueError):
        cap = 3
    times = _topic_research_times(findings)
    stale = [
        {"topic": t, "age_hours": (now - v["last"]) / 3600}
        for t, v in times.items()
        if (now - v["last"]) > stale_hours * 3600
    ]
    stale.sort(key=lambda s: s["age_hours"], reverse=True)
    return stale[:cap]


def _companion_priority(co_score) -> int:
    """Приоритет companion-задачи от co_score (0-10 → 50-90, кап 90).

    Риски (100) всегда выше companion-предсказаний — подтверждённая
    проблема важнее вероятной. Ниже порога 0.5 — базовая 50 (не нуль,
    чтобы задача не терялась в сортировке).
    """
    try:
        score = max(0.0, float(co_score))
    except (TypeError, ValueError):
        score = 0.0
    prio = 50 + int(score * 8)
    return min(prio, COMPANION_PRIORITY_CAP)


def _load_companions(patterns: dict) -> list[dict]:
    """Companion-предсказания из patterns.json (v4 global + v5 module).

    Паттерны, исторически приходящие ВМЕСТЕ с текущими ошибками — очередь
    делает из них пре-емптивные fix-задачи (grow point циклов 21-24:
    learner персистил companions, планировщик их не читал). Дедуп по
    pattern: выживает вариант с максимальным co_score (module 4.0 >
    global 2.0). Malformed записи (не dict / нет pattern / пустой)
    пропускаются молча.
    """
    out: dict[str, dict] = {}
    for entry in patterns.get("companions") or []:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        cur = out.get(pattern)
        score = entry.get("co_score", 0)
        if cur is None or score > cur.get("co_score", 0):
            out[pattern] = {
                "pattern": pattern,
                "co_score": score,
                "module": None,
                "priority": _companion_priority(score),
            }
    for entry in patterns.get("module_companions") or []:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        score = entry.get("co_score", 0)
        cur = out.get(pattern)
        if cur is None or score > cur.get("co_score", 0):
            out[pattern] = {
                "pattern": pattern,
                "co_score": score,
                "module": entry.get("source"),
                "priority": _companion_priority(score),
            }
    ranked = sorted(out.values(), key=lambda c: -c.get("co_score", 0))
    return ranked[:COMPANION_MAX_TASKS]


def load_state() -> dict:
    """Собрать состояние из всех источников."""
    state = {"pending": [], "risks": [], "knowledge_gaps": [], "errors_active": [],
             "companions": []}

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
            # Companion-предсказания (v4 global + v5 module): паттерны,
            # исторически приходящие вместе с текущими ошибками — пре-емптивные
            # fix-задачи (grow point циклов 21-24: learner персистил,
            # планировщик не читал).
            state["companions"] = _load_companions(patterns)
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
                            # Цикл 44: suggestion из predict_risks (напр.
                            # TWO-STRIKE RULE для tool_call_loop) переносится
                            # в задачу — раньше существовал только в отчёте
                            # learner'а, планировщик его выбрасывал.
                            "suggestion": _clean_suggestion(r.get("suggestion")),
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
            # Directed re-research: темы, исследованные > RESEARCH_STALE_HOURS
            # назад. Цикл 27: _pick_stale_topics — единый источник правды по
            # возрасту с _pick_gap_topic (одна тема = одна запись, возраст по
            # ПОСЛЕДНЕМУ исследованию): дубли находок больше не плодят дубли
            # задач, свежий re-research спасает тему от stale.
            state["stale_topics"] = _pick_stale_topics(
                knowledge.get("findings", []), now)
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


def _clean_suggestion(value) -> str | None:
    """Нормализовать suggestion из риска: строка с контентом → strip,
    иначе None (пустые/мусорные значения не попадают в задачи)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
            # Цикл 30: pattern несётся в задаче (как у companion, цикл 26) —
            # run_next использует его для авто-фидбека в learner без
            # парсинга текста задачи.
            "pattern": risk["pattern"],
            # Цикл 44: рекомендация из predict_risks (TWO-STRIKE RULE и т.п.)
            # — потребитель задачи (run_next/отчёт) получает совет, а не
            # только текст «Investigate and fix pattern».
            "suggestion": risk.get("suggestion"),
        })

    # 3. Companion-предсказания (grow point циклов 21-24): паттерны, которые
    # learner считает вероятными «следующими» (исторически приходят вместе
    # с активными ошибками). Пре-емптивные fix-задачи: приоритет от co_score
    # (50-90), всегда НИЖЕ подтверждённых рисков (100). Паттерн, уже
    # покрытый риск-задачей, пропускается (не дублируем). С кулдауном:
    # недавно исполненная companion-задача не пере-добавляется.
    risk_patterns = {r.get("pattern") for r in state.get("risks", []) if r.get("pattern")}
    for comp in state.get("companions", []):
        pattern = comp.get("pattern")
        if not pattern or pattern in risk_patterns:
            continue
        module = comp.get("module")
        if module:
            task_text = (f"Investigate and fix pattern: {pattern} "
                         f"(companion in {module})")
        else:
            task_text = (f"Investigate and fix pattern: {pattern} "
                         f"(companion of active errors)")
        if task_text in recent_tasks:
            continue
        queue.append({
            "task": task_text,
            "category": "fix",
            "priority": comp.get("priority", 60),
            "source": "companion",
            # v8 (цикл 26): pattern/module несутся в задаче — mark_completed
            # использует их для обратной связи в learner (фидбек по паре
            # без парсинга текста задачи).
            "pattern": pattern,
            "module": module,
        })

    # 4. Directed re-research: устаревшие темы из knowledge findings —
    # конкретная задача на тему (а не один generic "research cycle").
    # С кулдауном: если research по теме уже исполнялся (успех/фейл/
    # timeout), не пере-запускаем раньше DEFAULT_COOLDOWN — иначе
    # упавший поиск ре-квеился каждый цикл и долбил одну тему.
    for st in state.get("stale_topics", []):
        topic = st.get("topic")
        if not topic:
            continue
        task_text = f"Run curious agent research cycle for topic: {topic}"
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
            full_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            result = {
                "task": task_text,
                "status": "done" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "output_tail": full_output.strip()[-300:],
            }
            # Авто-фидбек companion (цикл 28): паттерн ЕСТЬ в выводе →
            # confirmed=True (предсказание сбылось), нет → confirmed=False
            # (опровергнуто). Детект по ПОЛНОМУ stdout+stderr (не только
            # tail): паттерн мог появиться в начале вывода. Только для
            # задач source="companion" с паттерном; не-companion задачи
            # фидбек не трогают. Ошибки learner'а — тихий отказ, задача
            # уже выполнена, очередь чистится ниже.
            if (task_item.get("source") == "companion" and _HAS_FEEDBACK
                    and task_item.get("pattern")):
                confirmed = task_item["pattern"].lower() in full_output.lower()
                try:
                    fb = _feedback_companion(
                        task_item["pattern"], confirmed,
                        module=task_item.get("module"))
                    result["feedback"] = fb
                    result["feedback_confirmed"] = confirmed
                except (OSError, ValueError, TypeError):
                    pass
            # Авто-фидбек risk (цикл 30): паттерн риска ЕСТЬ в выводе →
            # confirmed=True (риск реален, streak не трогаем), нет →
            # confirmed=False (опровергнут, streak снижается → задача
            # перестаёт ре-генерироваться из predict_risks). Только для
            # задач source="risk" с паттерном; ошибки learner'а — тихий
            # отказ, задача уже выполнена.
            if (task_item.get("source") == "risk" and _HAS_FEEDBACK
                    and task_item.get("pattern")):
                confirmed = task_item["pattern"].lower() in full_output.lower()
                try:
                    fb = _feedback_risk(task_item["pattern"], confirmed)
                    result["risk_feedback"] = fb
                    result["risk_confirmed"] = confirmed
                except (OSError, ValueError, TypeError):
                    pass
        except subprocess.TimeoutExpired:
            result = {"task": task_text, "status": "timeout", "exit_code": -1}
        except Exception as exc:  # noqa: BLE001
            result = {"task": task_text, "status": "error", "reason": str(exc)}

    # История + удаление выполненной задачи из очереди
    history = load_history()
    result["ts"] = time.time()
    result["ts_human"] = datetime.now(timezone.utc).isoformat()
    # Цикл 44: suggestion риска (TWO-STRIKE RULE и т.п.) едет в результате —
    # потребитель run_next видит совет, а не только статус/вывод.
    suggestion = task_item.get("suggestion")
    if suggestion:
        result["suggestion"] = suggestion
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


def mark_completed(task_description: str, confirmed: bool = True) -> dict | None:
    """Отметить задачу как выполненную (удалить из очереди).

    Для companion-задач (source="companion") замыкает петлю предсказаний
    (цикл 26): вызывает feedback_companion в error_pattern_learner —
    confirmed=True (паттерн реально наблюдался/исправлен) усиливает пары
    (anchor, pattern), False (предсказание не подтвердилось) ослабляет
    и со временем убирает companion из прогноза. Возвращает отчёт
    фидбека или None (не companion-задача / learner недоступен).
    """
    queue = load_queue()
    removed = [t for t in queue if t["task"] == task_description]
    queue = [t for t in queue if t["task"] != task_description]
    save_queue(queue)
    report = None
    for t in removed:
        if t.get("source") == "companion" and _HAS_FEEDBACK:
            pattern = t.get("pattern")
            if pattern:
                try:
                    report = _feedback_companion(pattern, confirmed,
                                                 module=t.get("module"))
                except (OSError, ValueError, TypeError):
                    # Фидбек не должен ломать завершение задачи: IO/JSON
                    # ошибки learner'а — тихий отказ, очередь всё равно чистится.
                    report = None
    return report


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
        # Цикл 44: рекомендация из predict_risks видна в отчёте сразу
        # (раньше совет learner'а жил только в его собственном отчёте)
        suggestion = item.get("suggestion")
        if suggestion:
            lines.append(f"     ↳ совет: {suggestion[:100]}")

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
