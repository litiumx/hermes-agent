#!/usr/bin/env python3
"""save_session_context.py — вызывается из крона каждые 30 мин.
Сохраняет ключевой контекст в session_bridge.json"""
import json, os, subprocess
from pathlib import Path

def get_current_context():
    ctx = {
        "timestamp": __import__('time').time(),
        "swarm_size": 3,
        "active_projects": ["FinForge", "Hermes", "Server"],
        "last_task": "session_bridge cron",
    }
    
    # Swarm
    try:
        r = subprocess.run(["python3", "/root/.hermes/agent/swarm.py", "get_size"], 
                          capture_output=True, text=True, timeout=5)
        ctx["swarm_size"] = int(r.stdout.strip())
    except: pass
    
    # Disk
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        ctx["disk_usage"] = r.stdout.strip().split("\n")[-1]
    except: pass
    
    # Memory
    try:
        r = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
        ctx["memory"] = r.stdout.strip().split("\n")[1]
    except: pass
    
    return ctx

def save():
    ctx = get_current_context()
    from session_bridge import save_context
    save_context(ctx)

if __name__ == "__main__":
    save()
    print("Context saved")
