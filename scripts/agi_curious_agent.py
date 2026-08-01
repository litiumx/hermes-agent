#!/usr/bin/env python3
"""agi_curious_agent.py — фоновый исследователь для автономного обучения.

Читает контекст сессий (через agi_session_bridge), находит темы для исследования,
сохраняет находки в knowledge.json. Может работать standalone или внутри Hermes.

Режимы:
- standalone: использует subprocess+curl для поиска
- hermes: импортируется и использует hermes_tools
"""

import json
import os
import subprocess
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone

KNOWLEDGE_FILE = Path("/root/.hermes/data/curious_knowledge.json")
BRIDGE_FILE = Path("/root/.hermes/session/bridge.json")
MAX_FINDINGS = 50  # ротация

# SQLite-first: читаем контекст через agi_session_bridge (канонический вид),
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

# Темы для авто-исследования при простое
DEFAULT_TOPICS = [
    "AI agent autonomous self-improvement best practices 2026",
    "Python pattern matching error handling strategies",
    "Linux process supervisor crash recovery patterns",
    "DeepSeek V4 model routing optimization",
    "MCP server reliability patterns",
]

RESEARCH_TRIGGERS = [
    "error", "fix", "bug", "optimize", "improve", "refactor",
    "security", "performance", "memory", "crash", "timeout",
]


def _ensure_dirs():
    KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_knowledge() -> dict:
    """Загрузить накопленные знания."""
    _ensure_dirs()
    if KNOWLEDGE_FILE.exists():
        try:
            return json.loads(KNOWLEDGE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"findings": [], "topics_searched": [], "last_search": 0}


def save_knowledge(data: dict):
    """Сохранить знания с ротацией."""
    _ensure_dirs()
    if len(data.get("findings", [])) > MAX_FINDINGS:
        data["findings"] = data["findings"][-MAX_FINDINGS:]
    if len(data.get("topics_searched", [])) > 100:
        data["topics_searched"] = data["topics_searched"][-100:]
    KNOWLEDGE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def get_active_topics() -> list[str]:
    """Извлечь активные темы из bridge-контекста (SQLite-first)."""
    topics = set()

    ctx = _load_bridge_context()
    if ctx:
        last_task = ctx.get("last_task", "")
        if last_task:
            for trigger in RESEARCH_TRIGGERS:
                if trigger.lower() in last_task.lower():
                    topics.add(last_task[:100])

        pending = ctx.get("pending_tasks", [])
        for task in pending[:3]:
            topics.add(task[:100])

        last_error = ctx.get("last_error", "")
        if last_error:
            # Извлекаем ключевые слова из ошибки
            words = [w for w in last_error.split() if len(w) > 3][:5]
            if words:
                topics.add(" ".join(words))

    # Если не нашли — используем дефолтные
    if not topics:
        idx = int(time.time() / 3600) % len(DEFAULT_TOPICS)
        topics.add(DEFAULT_TOPICS[idx])

    return list(topics)[:3]


def web_search_standalone(query: str) -> list[dict]:
    """Поиск через DuckDuckGo HTML (без API-ключа, бесплатно)."""
    import urllib.request
    import urllib.parse
    import re

    try:
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "HermesCuriousAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = []
        # Парсим результаты
        for match in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        ):
            url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            # Ищем сниппет рядом
            snippet_match = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html[match.end():match.end() + 2000], re.DOTALL
            )
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip() if snippet_match else ""
            results.append({"title": title, "url": url, "snippet": snippet[:300]})

        return results[:5]
    except Exception as e:
        return [{"error": str(e)}]


def research_topic(topic: str) -> dict:
    """Исследовать одну тему, вернуть находки."""
    findings = web_search_standalone(topic)
    return {
        "topic": topic,
        "timestamp": time.time(),
        "sources": findings,
    }


def run_research(force: bool = False) -> dict:
    """Главная функция: найти темы → исследовать → сохранить.

    Аргументы:
        force: если True, исследовать даже если недавно уже искали
    """
    knowledge = load_knowledge()

    # Проверяем, не искали ли недавно
    last = knowledge.get("last_search", 0)
    if not force and (time.time() - last) < 3600:
        return {
            "status": "skipped",
            "reason": f"Слишком рано. Последний поиск: {datetime.fromtimestamp(last).isoformat()}",
            "findings_count": len(knowledge.get("findings", [])),
        }

    topics = get_active_topics()
    new_findings = []

    for topic in topics:
        # Пропускаем уже исследованные
        if topic in knowledge.get("topics_searched", []):
            continue

        result = research_topic(topic)
        if result["sources"] and "error" not in str(result["sources"][0]):
            knowledge["findings"].append(result)
            new_findings.append(topic)
        knowledge["topics_searched"].append(topic)

    knowledge["last_search"] = time.time()
    save_knowledge(knowledge)

    return {
        "status": "ok" if new_findings else "no_new",
        "topics_researched": new_findings,
        "total_findings": len(knowledge.get("findings", [])),
        "next_topic": get_active_topics()[0] if get_active_topics() else "none",
    }


def get_report() -> str:
    """Человекочитаемый отчёт."""
    knowledge = load_knowledge()
    findings = knowledge.get("findings", [])

    lines = ["🧪 Curious Agent — Knowledge Report"]
    lines.append(f"  📚 Всего находок: {len(findings)}")
    lines.append(f"  🔍 Тем исследовано: {len(knowledge.get('topics_searched', []))}")

    if findings:
        lines.append(f"\n  Последние находки:")
        for f in findings[-3:]:
            ts = datetime.fromtimestamp(f["timestamp"]).strftime("%d.%m %H:%M")
            topic = f["topic"][:80]
            sources = len(f.get("sources", []))
            lines.append(f"    [{ts}] {topic} ({sources} источников)")

    return "\n".join(lines)


def search_knowledge(query: str) -> list[dict]:
    """Поиск по накопленным знаниям."""
    knowledge = load_knowledge()
    results = []
    query_lower = query.lower()

    for f in knowledge.get("findings", []):
        if query_lower in f["topic"].lower():
            results.append(f)
        else:
            for src in f.get("sources", []):
                snippet = src.get("snippet", "") + src.get("title", "")
                if query_lower in snippet.lower():
                    results.append(f)
                    break

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if query:
            results = search_knowledge(query)
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("Usage: agi_curious_agent.py search <query>")
    elif len(sys.argv) > 1 and sys.argv[1] == "force":
        result = run_research(force=True)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = run_research()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print()
        print(get_report())
