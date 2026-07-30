#!/usr/bin/env python3
"""
Auto Resume (cron version) — checks for interrupted sessions every 2 min.
Sends Telegram notification if a session was interrupted.
0 API tokens. Designed for */2 * * * * cron.
"""
import os, json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "353133098")
RESUME_FLAG = HERMES_HOME / "state" / "RESUME_AFTER_RESTART"
SENT_FLAG = HERMES_HOME / "state" / "resume_notification_sent"
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


def find_checkpoints():
    """Find all recent checkpoint files."""
    sessions_dir = HERMES_HOME / "sessions"
    if not sessions_dir.exists():
        return []

    checkpoints = []
    for sd in sessions_dir.iterdir():
        if sd.is_dir():
            cp = sd / "checkpoint.json"
            if cp.exists():
                try:
                    data = json.loads(cp.read_text())
                    ts_str = data.get("created_at", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str).timestamp()
                    else:
                        ts = cp.stat().st_mtime
                    checkpoints.append((ts, str(sd.name)))
                except Exception:
                    continue

    checkpoints.sort(reverse=True)
    return checkpoints


def main():
    now = datetime.now(MSK)

    # Check for RESUME_AFTER_RESTART flag (set by @reboot auto_resume)
    if RESUME_FLAG.exists():
        try:
            data = json.loads(RESUME_FLAG.read_text())
            session_id = data.get("session_id", "")
            if session_id:
                # Check if we already sent notification
                if SENT_FLAG.exists():
                    sent_data = json.loads(SENT_FLAG.read_text())
                    if sent_data.get("session_id") == session_id:
                        return  # already notified

                msg = (
                    f"🔄 <b>Гермес перезапущен</b>\n"
                    f"{now.strftime('%d.%m.%Y %H:%M MSK')}\n\n"
                    f"Обнаружена прерванная сессия: <code>{session_id[:12]}...</code>\n\n"
                    f"Отправь <code>/resume {session_id}</code> чтобы продолжить"
                )
                telegram_send(msg)

                # Mark as sent
                SENT_FLAG.parent.mkdir(parents=True, exist_ok=True)
                SENT_FLAG.write_text(json.dumps({
                    "session_id": session_id,
                    "sent_at": now.isoformat(),
                }))
        except Exception:
            pass
        finally:
            # Remove flag after processing
            try:
                RESUME_FLAG.unlink()
            except Exception:
                pass

    # Also check for any recent checkpoints (within 1 hour)
    checkpoints = find_checkpoints()
    for ts, sid in checkpoints[:3]:
        age = time.time() - ts
        if age < 3600 and not SENT_FLAG.exists():
            msg = (
                f"💾 <b>Найден чекпоинт сессии</b>\n"
                f"Сессия: <code>{sid[:12]}...</code>\n"
                f"Прервана: {int(age/60)} мин назад\n\n"
                f"Отправь <code>/resume --list</code> чтобы увидеть все"
            )
            telegram_send(msg)
            break


if __name__ == "__main__":
    main()
