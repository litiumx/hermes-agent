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

# Пути данных — env-переопределяемые (паттерн цикла 8: HERMES_HOME задаёт
# базу, AGI_*_FILE — точные файлы; песочница без прав на /root/.hermes).
HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
KNOWLEDGE_FILE = Path(os.environ.get("AGI_KNOWLEDGE_FILE",
                                     os.path.join(HERMES_HOME, "data/curious_knowledge.json")))
BRIDGE_FILE = Path(os.environ.get("AGI_BRIDGE_FILE",
                                  os.path.join(HERMES_HOME, "session/bridge.json")))
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

    stale = get_stale_topics(knowledge, max_age_days=30)
    if stale:
        lines.append(f"\n  🕸 Устаревших тем (>{30} дн.): {len(stale)}")
        for s in stale[:3]:
            lines.append(f"    [{s['age_days']} дн., score {s['score']:.1f}] {s['topic'][:60]}")
        if len(stale) > 3:
            lines.append(f"    … и ещё {len(stale) - 3}")
    else:
        lines.append("\n  🕸 Устаревших тем: нет")

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


def _topic_score(finding) -> float:
    """Оценка ценности находки (для решения stale: выбросить vs re-research).

    score = число источников (cap 5) + 0.5 за каждый источник с непустым
    snippet. Битые записи (не dict / без sources) → 0.0.
    """
    if not isinstance(finding, dict):
        return 0.0
    sources = finding.get("sources")
    if not isinstance(sources, list):
        return 0.0
    n = min(len(sources), 5)
    any_snippet = any(
        isinstance(s, dict) and s.get("snippet") for s in sources
    )
    return float(n) + (0.5 if any_snippet else 0.0)


def get_stale_topics(knowledge: dict | None = None,
                     max_age_days: float = 30) -> list[dict]:
    """Темы, исследованные давно (last_researched старше max_age_days).

    Возвращает список {"topic", "age_days", "score"} — самые старые первыми,
    при равном возрасте — меньший score первым (кандидаты на выброс/ре-поиск).
    Записи без timestamp / не-dict консервативно НЕ считаются stale
    (нет данных о возрасте — не трогаем).
    """
    if knowledge is None:
        knowledge = load_knowledge()
    cutoff = time.time() - max_age_days * 86400
    stale = []
    for f in knowledge.get("findings", []):
        if not isinstance(f, dict):
            continue
        ts = f.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if ts > cutoff:
            continue
        stale.append({
            "topic": f.get("topic", ""),
            "age_days": round((time.time() - ts) / 86400, 1),
            "score": _topic_score(f),
        })
    stale.sort(key=lambda s: (-s["age_days"], s["score"]))
    return stale


def prune_stale_topics(max_age_days: float = 60, min_score: float = 1.0) -> dict:
    """Удалить устаревшие находки с низкой ценностью.

    stale + score < min_score → удаляются (и из topics_searched). Свежие и
    stale-ценные (score >= min_score) остаются — они кандидаты на re-research
    через directed-очередь, а не на выброс. Идемпотентно.
    Возвращает {"removed", "kept", "re_research"}: re_research — список
    {"topic", "age_days", "score"} сохранённых stale-ценных тем (старые
    первыми) — их надо закинуть в очередь как re-research задачи.
    """
    knowledge = load_knowledge()
    cutoff = time.time() - max_age_days * 86400
    kept, removed, re_research = [], [], []
    for f in knowledge.get("findings", []):
        is_stale = (isinstance(f, dict) and isinstance(f.get("timestamp"), (int, float))
                    and f["timestamp"] <= cutoff)
        if is_stale and _topic_score(f) < min_score:
            removed.append(f.get("topic", ""))
        else:
            kept.append(f)
            if is_stale and isinstance(f, dict):
                re_research.append({
                    "topic": f.get("topic", ""),
                    "age_days": round((time.time() - f["timestamp"]) / 86400, 1),
                    "score": _topic_score(f),
                })
    re_research.sort(key=lambda r: (-r["age_days"], r["score"]))
    removed_set = set(removed)
    knowledge["findings"] = kept
    knowledge["topics_searched"] = [
        t for t in knowledge.get("topics_searched", []) if t not in removed_set
    ]
    save_knowledge(knowledge)
    return {"removed": len(removed), "kept": len(kept), "re_research": re_research}


def enqueue_re_research(re_research: list[dict], max_topics: int = 3) -> dict:
    """Закинуть ценные stale-темы в очередь как directed re-research задачи.

    Ленивый импорт agi_self_directed_queue: если модуль недоступен
    (изолированная песочница), prune НЕ падает — возвращаем enqueued=0
    и причину в "error". Приоритет задачи растёт с возрастом темы
    (cap 55), как в build_queue для stale_topics.
    """
    if not re_research:
        return {"enqueued": 0, "candidates": 0}
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from agi_self_directed_queue import enqueue_topic
    except Exception as exc:  # noqa: BLE001 — песочница без queue-модуля
        return {"enqueued": 0, "candidates": len(re_research), "error": str(exc)}
    enqueued = 0
    for item in re_research[:max_topics]:
        topic = item.get("topic", "")
        if not topic:
            continue
        priority = min(30 + int(item.get("age_days", 0) * 2), 55)
        if enqueue_topic(topic, priority=priority):
            enqueued += 1
    return {"enqueued": enqueued, "candidates": min(len(re_research), max_topics)}


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
    elif argv and argv[0] == "stale":
        # Список устаревших тем (кандидаты на re-research или выброс)
        max_age = float(argv[1]) if len(argv) > 1 else 30.0
        stale = get_stale_topics(max_age_days=max_age)
        print(json.dumps(stale, indent=2, ensure_ascii=False))
    elif argv and argv[0] == "prune":
        # Удалить stale-находки с низким score; ценные stale-темы
        # автоматически ставятся в очередь как directed re-research
        # (argv[3] == "0" отключает enqueue).
        max_age = float(argv[1]) if len(argv) > 1 else 60.0
        min_score = float(argv[2]) if len(argv) > 2 else 1.0
        result = prune_stale_topics(max_age_days=max_age, min_score=min_score)
        if len(argv) <= 3 or argv[3] != "0":
            result["re_research_enqueued"] = enqueue_re_research(
                result.get("re_research", []))
        else:
            result["re_research_enqueued"] = {
                "enqueued": 0,
                "candidates": len(result.get("re_research", [])),
                "disabled": True,
            }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = run_research()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print()
        print(get_report())


if __name__ == "__main__":
    main()
