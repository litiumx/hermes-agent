#!/usr/bin/env python3
"""
Авто-восстановление сессии после перезагрузки VPS или падения gateway.
Запускается при старте системы. 0 токенов API.
"""
import os, json, subprocess, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
STATE_FILE = HERMES_HOME / "state" / "pending_resume.json"
GATEWAY_PID_FILE = HERMES_HOME / "gateway.pid"
GATEWAY_LOCK = HERMES_HOME / "gateway.lock"
RESUME_FLAG = HERMES_HOME / "state" / "RESUME_AFTER_RESTART"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "353133098")

MSK = timezone(timedelta(hours=3))


def telegram_send(msg):
    if not TELEGRAM_BOT_TOKEN:
        return
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception:
        pass


def find_last_session():
    """Находит последнюю активную сессию."""
    sessions_dir = HERMES_HOME / "sessions"
    if not sessions_dir.exists():
        return None

    # Читаем sessions.db через sqlite3
    import sqlite3
    db_path = HERMES_HOME / "sessions.db"
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT session_id, updated_at FROM sessions "
            "WHERE status = 'active' OR status = 'interrupted' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def find_checkpoint():
    """Ищет последний checkpoint в sessions/*/."""
    sessions_dir = HERMES_HOME / "sessions"
    if not sessions_dir.exists():
        return None

    checkpoints = []
    for sd in sessions_dir.iterdir():
        if sd.is_dir():
            cp = sd / "checkpoint.json"
            if cp.exists():
                try:
                    data = json.loads(cp.read_text())
                    ts = data.get("timestamp", 0)
                    checkpoints.append((ts, str(sd.name), cp))
                except Exception:
                    continue

    checkpoints.sort(reverse=True)
    return checkpoints[0] if checkpoints else None


def check_gateway_alive():
    """Проверяет жив ли gateway. Поддерживает JSON PID (Hermes 0.18+) и plain PID."""
    if not GATEWAY_PID_FILE.exists():
        return False
    try:
        raw = GATEWAY_PID_FILE.read_text().strip()
        # Hermes 0.18+ writes JSON: {"pid": 2577566, "kind": "hermes-gateway", ...}
        if raw.startswith('{'):
            data = json.loads(raw)
            pid = data['pid']
        else:
            pid = int(raw)
        os.kill(pid, 0)
        return True
    except (ValueError, KeyError, json.JSONDecodeError, OSError):
        return False


def restart_gateway():
    """Перезапускает gateway."""
    # Убиваем старый лок-файл если есть
    if GATEWAY_LOCK.exists():
        GATEWAY_LOCK.unlink()

    subprocess.run(
        ["hermes", "gateway", "restart"],
        capture_output=True, timeout=30
    )
    time.sleep(3)
    return check_gateway_alive()


def resume_session(session_id: str) -> bool:
    """Пытается продолжить сессию через Telegram."""
    now = datetime.now(MSK)
    msg = (
        f"🔄 <b>Гермес перезапущен</b>\n"
        f"{now.strftime('%d.%m.%Y %H:%M MSK')}\n\n"
        f"Обнаружена прерванная сессия: <code>{session_id[:12]}...</code>\n\n"
        f"Отправь <code>/resume {session_id}</code> чтобы продолжить\n"
        f"Или <code>/resume --list</code> чтобы увидеть все сессии"
    )
    telegram_send(msg)
    return True


def main():
    print(f"[auto_resume] {datetime.now(MSK).isoformat()}")

    # 1. Проверяем gateway
    if not check_gateway_alive():
        print("Gateway is dead, restarting...")
        if restart_gateway():
            print("Gateway restarted")
        else:
            print("FAILED to restart gateway")
            telegram_send("⚠️ Гермес: не удалось перезапустить gateway после ребута")
            return

    # 2. Ищем прерванную сессию
    checkpoint = find_checkpoint()
    if checkpoint:
        ts, session_id, cp_path = checkpoint
        age = time.time() - ts
        if age < 86400:  # Не старше суток
            print(f"Found checkpoint: {session_id} (age: {age/3600:.1f}h)")
            # Пишем флаг для Telegram бота
            RESUME_FLAG.parent.mkdir(parents=True, exist_ok=True)
            RESUME_FLAG.write_text(json.dumps({
                "session_id": session_id,
                "timestamp": ts,
                "checkpoint": str(cp_path),
                "auto_resume": True
            }))

            # Шлём уведомление
            resume_session(session_id)
            print(f"Resume notification sent for {session_id}")
        else:
            print(f"Checkpoint too old: {session_id} ({age/3600:.1f}h)")

    # 3. Находим активную сессию
    last_session = find_last_session()
    if last_session and not checkpoint:
        print(f"Last active session: {last_session}")
        resume_session(last_session)
    elif not last_session and not checkpoint:
        print("No sessions to resume")

    print("[auto_resume] done")


if __name__ == "__main__":
    main()
