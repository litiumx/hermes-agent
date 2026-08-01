#!/usr/bin/env python3
"""
agi_focus_agent.py — внедрение принципов Focus Agent (arXiv 2601.07190).

Идея: LLM-агент сам решает когда консолидировать знания в Knowledge block
и прунить сырую историю. Slime-mold (Physarum) вдохновение.

Внедрение в Hermes:
1. Следит за размером сессии (state.db / context)
2. При достижении порога — сохраняет Knowledge block
3. Предлагает /compact (ручной триггер остаётся)
4. Ведёт историю компрессий (как Focus: 6 компрессий на задачу)

Результаты Focus: 22.7% экономия токенов, точность сохранена.
"""
import json, os, sqlite3, time, subprocess
from datetime import datetime
from pathlib import Path

HERMES_HOME = Path("/root/.hermes")
KB_FILE = HERMES_HOME / "data" / "knowledge_block.json"
HISTORY_FILE = HERMES_HOME / "data" / "focus_history.json"
SESSION_STATE = HERMES_HOME / "state.db"

# Пороги (из Focus: активная компрессия лучше пассивной)
TOKEN_WARN = 0.50   # 50% окна — пора думать о сжатии
TOKEN_ACT = 0.65    # 65% — действовать
MAX_COMPRESSIONS_PER_TASK = 6  # как в Focus

def get_context_usage():
    """Оценить заполнение контекста из state.db (строки сессии)."""
    try:
        conn = sqlite3.connect(SESSION_STATE)
        cur = conn.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]
        conn.close()
        return total
    except Exception:
        return 0

def load_kb():
    if KB_FILE.exists():
        return json.load(open(KB_FILE))
    return {"created": datetime.now().isoformat(), "knowledge": []}

def save_kb(kb):
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

def auto_focus_cycle():
    """Главный цикл — вызывать из крона каждые 30 мин."""
    msgs = get_context_usage()
    kb = load_kb()
    result = {
        "timestamp": datetime.now().isoformat(),
        "messages_in_db": msgs,
        "knowledge_entries": len(kb["knowledge"]),
        "action": "none"
    }

    # Оценка по количеству сообщений (эвристика: 200 сообщений ≈ 50% окна)
    if msgs > 600:  # >65% окна
        result["action"] = "compact_advised"
        result["advice"] = suggest_compact(f"контекст большой ({msgs} сообщений)")
    elif msgs > 450:  # >50%
        result["action"] = "watch"
        result["advice"] = f"контекст растёт: {msgs} сообщений, следим"

    return result

if __name__ == "__main__":
    print(json.dumps(auto_focus_cycle(), ensure_ascii=False, indent=2))
