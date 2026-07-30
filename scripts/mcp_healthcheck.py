#!/usr/bin/env python3
"""MCP Health Check — проверяет все MCP сервера и пишет отчёт"""
import subprocess, json, os, sys
from datetime import datetime

MCP_SERVERS = ["notebooklm", "paperclip", "travelpayouts", "playwright"]
LOG_FILE = os.path.expanduser("~/.hermes/logs/mcp-healthcheck.log")
MAX_FAILS = 3  # сколько раз подряд может упасть перед алертом

def test_server(name):
    r = subprocess.run(
        ["hermes", "mcp", "test", name],
        capture_output=True, text=True, timeout=10
    )
    ok = "✓ Connected" in r.stdout or "Connected" in r.stdout
    return ok, r.stdout[:200]

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    results = {}
    all_ok = True
    
    for s in MCP_SERVERS:
        ok, detail = test_server(s)
        results[s] = {"ok": ok}
        if not ok:
            all_ok = False
            results[s]["error"] = detail
    
    # Сводка
    status = "✅ ALL OK" if all_ok else "❌ PROBLEMS"
    print(f"[MCP HEALTH] {now} — {status}")
    for s, r in results.items():
        print(f"  {'✅' if r['ok'] else '❌'} {s}")
    
    # Лог
    with open(LOG_FILE, "a") as f:
        f.write(f"{now} — {status}\n")
        for s, r in results.items():
            f.write(f"  {'OK' if r['ok'] else 'FAIL'} {s}\n")
    
    # Если проблемы — пишем в IMPROVEMENT_QUEUE
    if not all_ok:
        queue = os.path.expanduser("~/.hermes/IMPROVEMENT_QUEUE.md")
        with open(queue, "a") as f:
            f.write(f"\n## [MCP_FAIL] HIGH — {now}\n")
            for s, r in results.items():
                if not r['ok']:
                    f.write(f"**{s}** не отвечает\n")
            f.write("---\n")
    
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
