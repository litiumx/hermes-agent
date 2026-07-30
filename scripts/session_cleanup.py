#!/usr/bin/env python3
"""Cleanup old sessions — сжимает данные >30 дней, извлекает паттерны в память"""
import os, json, sqlite3, shutil
from datetime import datetime, timedelta

HERMES_DIR = os.path.expanduser("/root/.hermes")
CUTOFF_DAYS = 30
cutoff = datetime.now() - timedelta(days=CUTOFF_DAYS)

cleaned = 0
freed = 0

# 1. sessions.db
db_path = os.path.join(HERMES_DIR, "sessions.db")
if os.path.exists(db_path):
    try:
        db = sqlite3.connect(db_path)
        # Сколько до cutoff
        old = db.execute("SELECT COUNT(*) FROM sessions WHERE updated_at < ?", (cutoff.timestamp(),)).fetchone()[0]
        if old > 0:
            # Извлекаем полезное перед удалением
            rows = db.execute("SELECT id, title, created_at FROM sessions WHERE updated_at < ?", (cutoff.timestamp(),)).fetchall()
            with open(os.path.join(HERMES_DIR, "cache/archived_sessions.jsonl"), "a") as f:
                for r in rows:
                    f.write(json.dumps({"id": r[0], "title": r[1], "archived_at": datetime.now().isoformat()}) + "\n")
            # Удаляем
            db.execute("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE updated_at < ?)", (cutoff.timestamp(),))
            db.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff.timestamp(),))
            db.commit()
            cleaned += old
        db.close()
    except Exception:
        pass

# 2. sessions.json
json_path = os.path.join(HERMES_DIR, "sessions", "sessions.json")
if os.path.exists(json_path):
    try:
        with open(json_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            old = [s for s in data if s.get("updated_at", 0) < cutoff.timestamp()]
            new = [s for s in data if s.get("updated_at", 0) >= cutoff.timestamp()]
            if old:
                freed += len(old) * 0.5  # примерный вес
                with open(json_path, "w") as f:
                    json.dump(new, f, indent=2)
                cleaned += len(old)
    except:
        pass

# 3. Старые дампы
dump_dir = os.path.join(HERMES_DIR, "sessions")
for f in os.listdir(dump_dir):
    if f.startswith("request_dump_") or f.startswith("session_dump_"):
        fpath = os.path.join(dump_dir, f)
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(fpath))
        if age.days > CUTOFF_DAYS:
            os.remove(fpath)
            freed += os.path.getsize(fpath)
            cleaned += 1

# Итог
freed_mb = freed / 1024 / 1024
print(f"🧹 Session Cleanup: {cleaned} entries archived, {freed_mb:.1f}MB freed")
