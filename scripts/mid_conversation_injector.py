#!/usr/bin/env python3
"""
Mid-Conversation System Message Injector — Claude Code Opus 4.8 parity.
Эмулирует mid-conversation system messages через форк сессий + fallback.

Стратегии (в порядке приоритета):
1. FORK: создать новую сессию с обновлённым system prompt (критические изменения)
2. USER_INJECT: добавить [СИСТЕМНАЯ ИНСТРУКЦИЯ] в user message
3. MEMORY: сохранить в persistent memory для следующих сессий

Использование:
  python3 mid_conversation_injector.py inject "новые инструкции" --priority critical
  python3 mid_conversation_injector.py inject "новые инструкции" --priority normal
  python3 mid_conversation_injector.py status
"""
import os, sys, json, subprocess
from datetime import datetime, timezone

HERMES_DIR = os.path.expanduser("/root/.hermes")
STATE_FILE = os.path.join(HERMES_DIR, "state", "mid_conversation_state.json")
MAX_INJECTIONS = 50

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"injections": [], "active_directives": []}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def inject_fork(instruction):
    """Стратегия 1: Форк сессии с новым system prompt."""
    # Сохраняем текущий контекст
    state = load_state()
    state["active_directives"].append({
        "instruction": instruction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": "fork"
    })
    save_state(state)

    # Создаём форк (через hermes CLI)
    try:
        result = subprocess.run(
            ["hermes", "fork", "--message", instruction],
            capture_output=True, text=True, timeout=30
        )
        return {"method": "fork", "status": "ok", "output": result.stdout}
    except Exception as e:
        return {"method": "fork", "status": "error", "error": str(e)}

def inject_user_message(instruction):
    """Стратегия 2: Инъекция через user message."""
    state = load_state()
    state["active_directives"].append({
        "instruction": instruction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": "user_inject"
    })

    # Ограничиваем количество инъекций
    if len(state["active_directives"]) > MAX_INJECTIONS:
        # Оставляем последние 10 + критические
        critical = [d for d in state["active_directives"] if d.get("priority") == "critical"]
        recent = state["active_directives"][-10:]
        state["active_directives"] = critical + recent

    save_state(state)

    formatted = f"\n[СИСТЕМНАЯ ИНСТРУКЦИЯ #{len(state['active_directives'])}]\n{instruction}\n\nВАЖНО: Это дополнение к предыдущим инструкциям. Продолжай работу с учётом этого обновления.\n"

    return {"method": "user_inject", "status": "ok", "formatted_message": formatted}

def inject_memory(instruction):
    """Стратегия 3: Persistent memory для долгосрочных изменений."""
    try:
        subprocess.run(
            ["hermes", "memory", "add", instruction],
            capture_output=True, text=True, timeout=10
        )
        return {"method": "memory", "status": "ok"}
    except Exception as e:
        return {"method": "memory", "status": "error", "error": str(e)}

def inject(instruction, priority="normal"):
    """Главный метод инъекции. Выбирает стратегию по приоритету."""
    if priority == "critical":
        # Пробуем форк, fallback → user inject
        result = inject_fork(instruction)
        if result["status"] == "ok":
            return result
        result = inject_user_message(instruction)
        inject_memory(instruction)
        return result
    else:
        # Обычный приоритет → user inject + memory
        result = inject_user_message(instruction)
        inject_memory(instruction)
        return result

def status():
    """Показывает активные директивы."""
    state = load_state()
    print(f"Активных директив: {len(state['active_directives'])}")
    for i, d in enumerate(state["active_directives"][-5:]):
        print(f"  #{i+1} [{d.get('method', '?')}] {d['instruction'][:80]}...")

def get_active_directives():
    """Возвращает все активные директивы для вставки в промпт."""
    state = load_state()
    if not state["active_directives"]:
        return ""

    lines = ["\n## АКТИВНЫЕ ДИРЕКТИВЫ (mid-conversation)"]
    for i, d in enumerate(state["active_directives"][-5:]):
        lines.append(f"{i+1}. {d['instruction']}")
    return "\n".join(lines)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "inject":
        instruction = sys.argv[2]
        priority = "normal"
        if "--priority" in sys.argv:
            idx = sys.argv.index("--priority")
            priority = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "normal"
        result = inject(instruction, priority)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "status":
        status()
    elif cmd == "directives":
        print(get_active_directives())
    elif cmd == "clear":
        save_state({"injections": [], "active_directives": []})
        print("Cleared")
