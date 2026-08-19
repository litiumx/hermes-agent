#!/usr/bin/env python3
"""
agi_focus_agent.py — внедрение принципов Focus Agent (arXiv 2601.07190)
+ интеграция headroom (63K⭐) — сжатие контента перед сохранением в память.

Focus: агент сам решает когда консолидировать знания и прунить историю.
Headroom: 60-95% меньше токенов на JSON, точность сохранена (GSM8K ±0.000).

Внедрение в Hermes:
1. Следит за размером сессии
2. Сжимает большие JSON/tool-output через headroom перед сохранением
3. Ведёт Knowledge block + историю компрессий
"""
import importlib
import json, os, sqlite3, time, subprocess
from datetime import datetime
from pathlib import Path

HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
KB_FILE = Path(os.environ.get("AGI_KB_FILE",
                              os.path.join(HERMES_HOME, "data/knowledge_block.json")))
HISTORY_FILE = Path(os.environ.get("AGI_HISTORY_FILE",
                                   os.path.join(HERMES_HOME, "data/focus_history.json")))
SESSION_STATE = Path(os.environ.get("AGI_SESSION_STATE",
                                    os.path.join(HERMES_HOME, "state.db")))

TOKEN_WARN = 0.50
TOKEN_ACT = 0.65
MAX_COMPRESSIONS_PER_TASK = 6
COMPACT_COOLDOWN_H = 6    # не чаще раза в 6 часов реальной компакции
SUGGEST_COOLDOWN_H = 3    # не чаще раза в 3 часа повторных советов "watch"
ESCALATION_COOLDOWN_H = 24  # эскалация зацикливания — не чаще раза в сутки
MEMORY_MAINTAIN_COOLDOWN_H = 24  # ретеншн памяти — раз в сутки
REPEAT_WINDOW_SEC = 7200   # окно поиска повторов tool calls (2 часа)
REPEAT_MIN = 3             # минимум одинаковых вызовов = «повтор»
REPEAT_TOP = 5             # сколько повторяющихся сигнатур показывать

def compress_with_headroom(text: str, min_len: int = 500) -> str:
    """Сжать текст через headroom library. Если <min_len или ошибка — вернуть как есть."""
    if len(text) < min_len:
        return text
    try:
        from headroom import compress
        result = compress([{"role": "tool", "tool_call_id": "c", "content": text}])
        msgs = result.messages if hasattr(result, "messages") else result
        for m in msgs:
            if m.get("role") == "tool":
                c = m.get("content", "")
                if c and len(c) < len(text):
                    return c
        return text
    except Exception:
        return text

def get_context_usage():
    """Оценить заполнение контекста из state.db: (кол-во сообщений, оценка токенов)."""
    try:
        conn = sqlite3.connect(SESSION_STATE)
        cur = conn.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]
        # Токен-эстимация по реальному содержимому: ~4 символа на токен
        # (COUNT(*) ненадёжен — это счётчик turns, а не объём)
        tokens = 0
        try:
            cur = conn.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM messages")
            chars = cur.fetchone()[0]
            tokens = int(chars) // 4
        except Exception:
            tokens = total * 250  # fallback: ~250 токенов на сообщение
        conn.close()
        return total, tokens
    except Exception:
        return 0, 0

def load_kb():
    if KB_FILE.exists():
        return json.load(open(KB_FILE))
    return {"created": datetime.now().isoformat(), "knowledge": []}

def save_kb(kb):
    KB_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(kb, open(KB_FILE, "w"), ensure_ascii=False, indent=2)

def add_knowledge(topic: str, content: str, source: str = "auto"):
    """Добавить знание в Knowledge block (как Focus Knowledge)."""
    kb = load_kb()
    kb["knowledge"].append({
        "topic": topic,
        "content": content[:2000],
        "source": source,
        "timestamp": datetime.now().isoformat()
    })
    # Ограничение: храним топ-100 знаний
    if len(kb["knowledge"]) > 100:
        kb["knowledge"] = kb["knowledge"][-100:]
    save_kb(kb)
    return len(kb["knowledge"])

def _log_event(entry: dict):
    hist = []
    if HISTORY_FILE.exists():
        try:
            hist = json.load(open(HISTORY_FILE))
        except Exception:
            hist = []
    hist.append(entry)
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(hist[-50:], open(HISTORY_FILE, "w"), ensure_ascii=False, indent=2)


def compact_knowledge(max_entries: int = 100, max_age_days: int = 30) -> dict:
    """Авто-компрессия Knowledge block (детерминированная, без LLM):
    1. Дедуп по topic — оставить свежайшую запись, источники склеить.
    2. Прунинг старше max_age_days — ТОЛЬКО если записей больше max_entries
       (иначе старые знания не трогаем: они могут быть единственным следом).
    3. Финальный кап max_entries (старые первыми, счётчик capped).
    4. Headroom-сжатие контента >2000 символов (fallback — срез).
    Возвращает честную статистику removals (deduped/pruned_old/capped).
    Пустое ничего не трогает (changed=False, без записи в history)."""
    kb = load_kb()
    raw = kb["knowledge"]
    before = len(raw)
    if before == 0:
        return {"before": 0, "after": 0, "deduped": 0, "pruned_old": 0,
                "capped": 0, "compressed": 0, "changed": False}
    now_ts = time.time()
    cutoff = now_ts - max_age_days * 86400

    def ts_of(e):
        try:
            return datetime.fromisoformat(e.get("timestamp", "")).timestamp()
        except Exception:
            return 0

    # 1. дедуп по topic — свежайшая побеждает, источники склеиваем
    by_topic = {}
    for e in raw:
        topic = e.get("topic", "untitled")
        cur = by_topic.get(topic)
        if cur is None or ts_of(e) > ts_of(cur):
            if cur is not None:
                srcs = set()
                for s in (cur.get("source", ""), e.get("source", "")):
                    if s:
                        srcs.add(s)
                e = dict(e)
                e["source"] = ",".join(sorted(srcs))
            by_topic[topic] = e
    entries = sorted(by_topic.values(), key=ts_of, reverse=True)
    deduped = before - len(entries)

    # 2. прунинг по возрасту — только при превышении лимита
    pruned_old = 0
    if len(entries) > max_entries:
        keep = [e for e in entries if ts_of(e) >= cutoff]
        pruned_old = len(entries) - len(keep)
        entries = keep

    # 3. финальный кап
    capped = 0
    if len(entries) > max_entries:
        capped = len(entries) - max_entries
        entries = entries[:max_entries]

    # 4. headroom-сжатие крупных записей
    compressed = 0
    for e in entries:
        c = e.get("content", "")
        if len(c) > 2000:
            cc = compress_with_headroom(c, min_len=2000)
            if cc != c and len(cc) < len(c):
                e["content"] = cc
                compressed += 1

    changed = len(entries) != before or bool(compressed)
    if changed:
        kb["knowledge"] = entries
        save_kb(kb)
        _log_event({
            "time": datetime.now().isoformat(),
            "type": "compaction",
            "before": before,
            "after": len(entries),
            "deduped": deduped,
            "pruned_old": pruned_old,
            "capped": capped,
            "compressed": compressed,
        })
    return {"before": before, "after": len(entries), "deduped": deduped,
            "pruned_old": pruned_old, "capped": capped, "compressed": compressed,
            "changed": changed}


def suggest_compact(reason: str):
    """Записать рекомендацию по сжатию + применить если возможно."""
    hist = []
    if HISTORY_FILE.exists():
        hist = json.load(open(HISTORY_FILE))
    hist.append({
        "time": datetime.now().isoformat(),
        "reason": reason,
        "type": "suggestion"
    })
    json.dump(hist[-50:], open(HISTORY_FILE, "w"), ensure_ascii=False, indent=2)

    # Если у нас есть активная задача — автоматическая компрессия знаний
    return f"💡 Focus: {reason}. Knowledge block: {len(load_kb()['knowledge'])} записей."

def _parse_ts(value) -> float:
    """created_at → epoch float. Поддерживает float/ISO (в т.ч. Z-суффикс).
    0 при нечитаемом — строка пропускается, не крах."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value)
    except ValueError:
        pass
    try:
        s = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def detect_repeated_calls(db_path=None, window_sec=REPEAT_WINDOW_SEC,
                          min_repeats=REPEAT_MIN, top=REPEAT_TOP):
    """Детект повторяющихся tool calls в state.db — ранний сигнал деградации
    контекста (SELF_IMPROVE 14.08 #2: на ~80K токенов в multi-step прогоне
    начинаются повторяющиеся tool calls; компактить ПЕРВЫМ делом, не дожидаясь
    порога 70%). Считает одинаковые сигнатуры (tool + аргументы с
    нормализацией порядка ключей) в окне window_sec.

    Возвращает список dict {tool, args, count} (топ по count) или [] при
    ошибке/пустоте. Тихий отказ: нет БД/таблицы/колонки/битый JSON/битый ts —
    всё пропускается, исключения не кидаются."""
    db = db_path or SESSION_STATE
    cutoff = time.time() - window_sec
    sig_counts = {}
    sig_example = {}
    try:
        conn = sqlite3.connect(db)
        cur = conn.execute(
            "SELECT role, tool_calls, created_at FROM messages")
        for role, tc_json, created_at in cur.fetchall():
            if role != "assistant" or not tc_json:
                continue
            ts = _parse_ts(created_at)
            if ts <= 0 or ts < cutoff:
                continue
            try:
                calls = json.loads(tc_json)
            except Exception:
                continue
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool = call.get("name")
                args = call.get("arguments")
                if not isinstance(tool, str) or args is None:
                    continue
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        continue
                try:
                    sig = tool + "|" + json.dumps(args, sort_keys=True,
                                                  ensure_ascii=False)
                except Exception:
                    continue
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
                if sig not in sig_example:
                    sig_example[sig] = args
        conn.close()
    except Exception:
        return []
    hits = [{"tool": sig.split("|", 1)[0], "args": sig_example[sig],
             "count": cnt}
            for sig, cnt in sig_counts.items() if cnt >= min_repeats]
    hits.sort(key=lambda h: h["count"], reverse=True)
    return hits[:top]


def report_repeats_to_learner(repeats, learner_name="agi_error_pattern_learner") -> dict:
    """Передать подтверждённые повторы в error-pattern learner (цикл 42).

    Grow point SELF_IMPROVE 18.08: повторы tool calls — подтверждённый
    паттерн «зацикливание»; learner усиливает streak tool_call_loop:<tool>,
    чтобы predict_risks учитывал его. Ленивый импорт: модуль недоступен
    (песочница, другие env) → тихий отказ {"error": ...}, исключения не
    кидаются — focus-цикл не должен падать из-за learner."""
    try:
        mod = importlib.import_module(learner_name)
        fn = getattr(mod, "feedback_loop_evidence", None)
        if fn is None:
            return {"error": f"feedback_loop_evidence not found in {learner_name}"}
        return fn(repeats)
    except Exception as e:
        return {"error": f"learner unavailable: {e}"}


def escalate_loop(repeats, top_hit=None) -> dict:
    """Эскалация пользователю при зацикливании, которое НЕ сломала компакция
    (grow point 19.08): повторы tool calls продолжаются, хотя компакция была
    <COMPACT_COOLDOWN_H назад → совет уже не помогает, нужна ручная проверка.

    Пишет escalation-событие в history (спам-защита на уровне вызова —
    auto_focus_cycle проверяет ESCALATION_COOLDOWN_H). Возвращает
    {"message", "tool", "count"}. Пустые repeats → тихий отказ {"message": ""}
    БЕЗ записи в history; битые записи (нет tool) не роняют цикл."""
    if not repeats:
        return {"message": ""}
    top = top_hit or repeats[0]
    tool = top.get("tool", "?")
    count = top.get("count", 0)
    msg = (f"⚠️ Цикл продолжается после компакции: {tool} ×{count} за 2ч — "
           f"компакция не сломала зацикливание, нужна ручная проверка/остановка")
    _log_event({
        "time": datetime.now().isoformat(),
        "type": "escalation",
        "tool": tool,
        "count": count,
        "message": msg,
    })
    return {"message": msg, "tool": tool, "count": count}


def _last_event_time(event_type: str) -> float:
    """Когда было последнее событие type в history (0 если никогда)."""
    if not HISTORY_FILE.exists():
        return 0
    try:
        hist = json.load(open(HISTORY_FILE))
    except Exception:
        return 0
    for e in reversed(hist):
        if e.get("type") == event_type:
            try:
                return datetime.fromisoformat(e["time"]).timestamp()
            except Exception:
                return 0
    return 0


def _memory_maintenance():
    """Ежедневный ретеншн multi-tier памяти: retain (medium decay + long
    eviction) → consolidate (порядок важен: decay СНАЧАЛА, иначе consolidate
    промоутит нечитаемые medium в long по возрасту). None при ошибке — тихий
    отказ, не роняем цикл."""
    try:
        import agi_context_store as cs
        return {"retain": cs.retain_memory(),
                "consolidate": cs.consolidate_memory()}
    except Exception:
        return None


def auto_focus_cycle():
    """Главный цикл — вызывать из крона каждые 30 мин.
    С кулдаунами: компакция не чаще COMPACT_COOLDOWN_H, советы watch не чаще
    SUGGEST_COOLDOWN_H — иначе каждый прогон при большом контексте спамит."""
    msgs, tokens = get_context_usage()
    kb = load_kb()
    result = {
        "timestamp": datetime.now().isoformat(),
        "messages_in_db": msgs,
        "estimated_tokens": tokens,
        "knowledge_entries": len(kb["knowledge"]),
        "action": "none"
    }

    # Порог по токенам (1M окно DeepSeek V4) с фолбэком на счётчик сообщений
    tok_ratio = tokens / 1_000_000 if tokens else 0
    now_ts = time.time()

    # Grow point 29: ежедневный ретеншн памяти (medium decay → long eviction
    # → consolidate). Включён ТОЛЬКО при AGI_MAINTAIN_MEMORY=1 — защита от
    # случайного трогания prod-БД в тестах/песочнице. Кулдаун 24ч.
    if os.environ.get("AGI_MAINTAIN_MEMORY") == "1":
        last_m = _last_event_time("memory_maintain")
        if now_ts - last_m > MEMORY_MAINTAIN_COOLDOWN_H * 3600:
            maint = _memory_maintenance()
            if maint is not None:
                _log_event({"time": datetime.now().isoformat(),
                            "type": "memory_maintain"})
                result["memory_maintained"] = maint

    # Оценка по количеству сообщений (эвристика: 200 сообщений ≈ 50% окна)
    # Цикл 41 (SELF_IMPROVE 14.08 #2): повторяющиеся tool calls = ранний
    # сигнал деградации контекста (на ~80K токенов начинаются повторы) —
    # компактим ПЕРВЫМ делом, не дожидаясь токен-порога.
    repeats = detect_repeated_calls()
    result["repeated_calls"] = repeats
    if repeats:
        # Цикл 42: подтверждённое зацикливание → фидбек в error-pattern
        # learner (streak tool_call_loop:<tool>), даже если компакция в кулдауне.
        result["loop_feedback"] = report_repeats_to_learner(repeats)
        top_hit = repeats[0]
        last = _last_event_time("compaction")
        if now_ts - last > COMPACT_COOLDOWN_H * 3600:
            comp = compact_knowledge()
            result["action"] = "compacted" if comp["changed"] else "repeat_advised"
            result["compaction"] = comp
            result["advice"] = suggest_compact(
                f"повторяющиеся tool calls: {top_hit['tool']} ×{top_hit['count']} "
                f"за 2ч — контекст деградирует (ранний сигнал)")
        else:
            # Grow point 19.08: компакция была недавно, а повторы ВСЁ ЕЩЕ есть —
            # компакция не сломала цикл → эскалация пользователю (не просто
            # совет). Кулдаун эскалации 24ч — не спамим каждый прогон крона.
            last_esc = _last_event_time("escalation")
            if now_ts - last_esc > ESCALATION_COOLDOWN_H * 3600:
                result["escalation"] = escalate_loop(repeats, top_hit)
                result["action"] = "repeat_escalated"
                result["advice"] = result["escalation"]["message"]
            else:
                result["action"] = "repeat_cooldown"
                result["advice"] = (f"повторяющиеся tool calls ({top_hit['tool']} "
                                    f"×{top_hit['count']}), эскалация была "
                                    f"<{ESCALATION_COOLDOWN_H}ч назад — пропускаю")
    elif tok_ratio > TOKEN_ACT or msgs > 600:  # >65% окна
        last = _last_event_time("compaction")
        if now_ts - last > COMPACT_COOLDOWN_H * 3600:
            comp = compact_knowledge()
            result["action"] = "compacted" if comp["changed"] else "compact_advised"
            result["compaction"] = comp
            result["advice"] = suggest_compact(
                f"контекст большой ({msgs} сообщений, ~{tokens} токенов)")
        else:
            result["action"] = "compact_cooldown"
            result["advice"] = (f"контекст большой ({msgs} сообщений), но компакция "
                                f"была <{COMPACT_COOLDOWN_H}ч назад — пропускаю")
    elif tok_ratio > TOKEN_WARN or msgs > 450:  # >50%
        last = _last_event_time("suggestion")
        if now_ts - last > SUGGEST_COOLDOWN_H * 3600:
            result["action"] = "watch"
            result["advice"] = (f"контекст растёт: {msgs} сообщений, ~{tokens} токенов, "
                                f"следим")
            _log_event({
                "time": datetime.now().isoformat(),
                "type": "suggestion",
                "msgs": msgs, "tokens": tokens,
            })
        else:
            result["action"] = "watch_cooldown"

    return result

if __name__ == "__main__":
    print(json.dumps(auto_focus_cycle(), ensure_ascii=False, indent=2))
