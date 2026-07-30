#!/usr/bin/env python3
"""Автосохранение важных фактов в память Гермеса."""
import json, os, sys
from datetime import datetime

HERMES_DIR = "/root/.hermes"
LOG_FILE = os.path.join(HERMES_DIR, "logs/memory_autosave.log")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}")

if __name__ == "__main__":
    log("📝 Memory autosave check — nothing to extract automatically")
