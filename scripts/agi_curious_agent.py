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
import re
import subprocess
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone

KNOWLEDGE_FILE = Path("/root/.hermes/data/curious_knowledge.json")
BRIDGE_FILE = Path("/root/.hermes/session/bridge.json")
MAX_FINDINGS = 50  # ротация

# Rate-limit между поисками (анти-флуд DDG). Настраивается через env.
SEARCH_DELAY = float(os.environ.get("AGI_SEARCH_DELAY", "3.0"))      # между темами
FALLBACK_DELAY = float(os.environ.get("AGI_FALLBACK_DELAY", "1.0"))  # между источниками fallback-цепочки

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
    """Загрузить накопленные знания (с нормализацией типов).

    Битый JSON, валидный JSON не-dict (строка/список) и null-поля →
    безопасные дефолты. Раньше такие файлы возвращались как есть и
    run_research падал с TypeError (string indices / 'NoneType' not iterable).
    """
    _ensure_dirs()
    if KNOWLEDGE_FILE.exists():
        try:
            data = json.loads(KNOWLEDGE_FILE.read_text())
            if isinstance(data, dict):
                if not isinstance(data.get("findings"), list):
                    data["findings"] = []
                if not isinstance(data.get("topics_searched"), list):
                    data["topics_searched"] = []
                if not isinstance(data.get("last_search"), (int, float)):
                    data["last_search"] = 0
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"findings": [], "topics_searched": [], "last_search": 0}


def save_knowledge(data: dict):
    """Сохранить знания с ротацией (устойчив к None/не-list полям)."""
    _ensure_dirs()
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    topics = data.get("topics_searched") if isinstance(data.get("topics_searched"), list) else []
    if len(findings) > MAX_FINDINGS:
        findings = findings[-MAX_FINDINGS:]
    if len(topics) > 100:
        topics = topics[-100:]
    KNOWLEDGE_FILE.write_text(json.dumps(
        {"findings": findings, "topics_searched": topics,
         "last_search": data.get("last_search", 0)},
        indent=2, ensure_ascii=False))


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


def _extract_ddg_html(html: str) -> list[dict]:
    """Парсер html.duckduckgo.com (result__a + result__snippet)."""
    results = []
    for match in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        url = match.group(1)
        title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        snip = re.search(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            html[match.end():match.end() + 2000], re.DOTALL
        )
        snippet = re.sub(r'<[^>]+>', '', snip.group(1)).strip() if snip else ""
        results.append({"title": title, "url": url, "snippet": snippet[:300]})
    return results


def _extract_ddg_lite(html: str) -> list[dict]:
    """Парсер lite.duckduckgo.com (rel=nofollow ссылки + result-snippet)."""
    results = []
    for match in re.finditer(
        r'<a[^>]*rel="nofollow"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        url = match.group(1)
        if url.startswith("//"):
            url = "https:" + url
        title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        snip = re.search(
            r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
            html[match.end():match.end() + 3000], re.DOTALL
        )
        snippet = re.sub(r'<[^>]+>', '', snip.group(1)).strip() if snip else ""
        results.append({"title": title, "url": url, "snippet": snippet[:300]})
    return results


def _extract_ddg_api(html: str) -> list[dict]:
    """Парсер api.duckduckgo.com (Instant Answer: AbstractText/RelatedTopics)."""
    results = []
    try:
        data = json.loads(html)
    except (json.JSONDecodeError, TypeError):
        return results
    if data.get("AbstractText") and data.get("AbstractURL"):
        results.append({
            "title": data.get("Heading", "Instant Answer"),
            "url": data["AbstractURL"],
            "snippet": data["AbstractText"][:300],
        })
    for topic in data.get("RelatedTopics", []):
        if topic.get("Text") and topic.get("FirstURL"):
            results.append({
                "title": topic.get("Text", "")[:80],
                "url": topic["FirstURL"],
                "snippet": topic["Text"][:300],
            })
        elif topic.get("Topics"):
            for sub in topic["Topics"]:
                if sub.get("Text") and sub.get("FirstURL"):
                    results.append({
                        "title": sub.get("Text", "")[:80],
                        "url": sub["FirstURL"],
                        "snippet": sub["Text"][:300],
                    })
    return results


def web_search_standalone(query: str, source: str = "auto") -> list[dict]:
    """Поиск без API-ключа (бесплатно) с fallback-цепочкой.

    Цепочка: html.duckduckgo.com → lite.duckduckgo.com → api.duckduckgo.com
    (Instant Answer). html-версия DDG часто отдаёт 200 с anomaly-страницей
    без result__a ссылок — тогда пробуем следующие источники (фикс 04.08:
    раньше возвращался пустой список и curious_agent молча не учился).
    """
    import urllib.request
    import urllib.parse

    encoded = urllib.parse.quote(query)
    sources = [
        ("ddg_html", f"https://html.duckduckgo.com/html/?q={encoded}",
         "HermesCuriousAgent/1.0", _extract_ddg_html),
        ("ddg_lite", f"https://lite.duckduckgo.com/lite/?q={encoded}",
         "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", _extract_ddg_lite),
        ("ddg_api", f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1",
         "HermesCuriousAgent/1.0", _extract_ddg_api),
    ]

    errors = []
    for idx, (name, url, ua, parser) in enumerate(sources):
        if idx > 0:
            time.sleep(FALLBACK_DELAY)  # пауза между источниками fallback-цепочки
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            results = parser(body)
            # DDG html умеет отдавать 200 с anomaly-страницей — нужны реальные ссылки
            if results:
                return results[:5]
            errors.append(f"{name}: no results parsed")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")

    return [{"error": " | ".join(errors) or "no search sources available"}]


def research_topic(topic: str) -> dict:
    """Исследовать одну тему, вернуть находки."""
    findings = web_search_standalone(topic)
    return {
        "topic": topic,
        "timestamp": time.time(),
        "sources": findings,
    }


def run_research(force: bool = False, topics_override: list[str] | None = None) -> dict:
    """Главная функция: найти темы → исследовать → сохранить.

    Аргументы:
        force: если True, исследовать даже если недавно уже искали
        topics_override: если задан — исследовать ТОЛЬКО эти темы (directed
            re-research из очереди планировщика). Отключает кулдаун и пропуск
            уже исследованных тем: смысл directed-задачи — ОСВЕЖИТЬ stale-тему.
    """
    knowledge = load_knowledge()

    # Очистка отравленных записей: темы из topics_searched без findings
    # (результат сломанного поиска 02-04.08, когда тема помечалась
    # исследованной даже при 0 результатах) — такие темы снова доступны.
    # Безусловно: при 0 findings (пустой found_topics) topics_searched
    # тоже должен очиститься, иначе темы навсегда пропускаются.
    found_topics = {f.get("topic") for f in knowledge.get("findings", [])}
    knowledge["topics_searched"] = [
        t for t in knowledge.get("topics_searched", []) if t in found_topics
    ]

    # Проверяем, не искали ли недавно (directed-переопределение игнорирует кулдаун)
    last = knowledge.get("last_search", 0)
    if not force and not topics_override and (time.time() - last) < 3600:
        return {
            "status": "skipped",
            "reason": f"Слишком рано. Последний поиск: {datetime.fromtimestamp(last).isoformat()}",
            "findings_count": len(knowledge.get("findings", [])),
        }

    topics = topics_override if topics_override else get_active_topics()
    new_findings = []
    searched_any = False

    for topic in topics:
        # Пропускаем уже исследованные (кроме directed-переопределения —
        # там задача стоит на РЕ-исследование устаревшей темы)
        if topic in knowledge.get("topics_searched", []) and not topics_override:
            continue
        # Rate-limit: пауза ТОЛЬКО между реальными поисками (не после skip)
        if searched_any:
            time.sleep(SEARCH_DELAY)
        searched_any = True

        result = research_topic(topic)
        # ФИКС 04.08: раньше было "error" not in str(...) — подстрока в
        # сериализованном dict. Любой нормальный результат с 'errors' в
        # заголовке ('syntax errors', 'error handling') считался провалом
        # поиска → curious_agent никогда не сохранял findings.
        # Теперь: проверка по КЛЮЧУ dict (fail-результат = {"error": ...}).
        if result["sources"] and "error" not in result["sources"][0]:
            # Directed re-research: заменяем устаревшую находку на свежую
            # (без этого findings растут неограниченно при повторных циклах)
            knowledge["findings"] = [
                f for f in knowledge["findings"] if f.get("topic") != topic
            ]
            knowledge["findings"].append(result)
            new_findings.append(topic)
            # Дедуп: directed re-research (stale-темы) повторяет одну тему —
            # без удаления старых вхождений topics_searched забивается дублями
            # (кап 100 уходит на одну тему, отчёт врёт по числу тем).
            knowledge["topics_searched"] = [
                t for t in knowledge.get("topics_searched", []) if t != topic
            ]
            knowledge["topics_searched"].append(topic)
        # Провал поиска НЕ помечает тему исследованной — retry в следующем запуске

    knowledge["last_search"] = time.time()
    save_knowledge(knowledge)

    # Один вызов get_active_topics (лишний парсинг bridge контекста ни к чему)
    active = get_active_topics()
    return {
        "status": "ok" if new_findings else "no_new",
        "topics_researched": new_findings,
        "total_findings": len(knowledge.get("findings", [])),
        "next_topic": active[0] if active else "none",
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


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint (выделен для тестируемости)."""
    import sys

    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "search":
        query = " ".join(argv[1:]) if len(argv) > 1 else ""
        if query:
            results = search_knowledge(query)
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("Usage: agi_curious_agent.py search <query>")
    elif argv and argv[0] == "force":
        result = run_research(force=True)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif argv and argv[0] == "topic":
        # Directed re-research: исследовать ровно одну тему (из очереди
        # планировщика), даже если она уже исследована и кулдаун активен.
        topic = " ".join(argv[1:]).strip()
        if not topic:
            print("Usage: agi_curious_agent.py topic <topic>")
            sys.exit(1)
        result = run_research(force=True, topics_override=[topic])
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = run_research()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print()
        print(get_report())


if __name__ == "__main__":
    main()
