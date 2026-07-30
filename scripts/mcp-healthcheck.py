#!/usr/bin/env python3
"""MCP pre-flight healthcheck — проверяет все MCP сервера при старте.
Выводит: ✅ или ❌ для каждого. Таймаут 5s на сервер."""
import subprocess, json, sys, socket, os

MCP_SERVERS = {
    "notebooklm": ["unix", "/root/.notebooklm-mcp-cli/profiles/default/socket.sock"],
    "paperclip": ["stdio", "/usr/local/bin/paperclip-mcp.sh"],
    "travelpayouts": ["stdio", "npx", "-y", "@analyticalObserver/travelpayouts-mcp"],
    "router": ["stdio", "python3", "/root/.hermes/routerich_mcp_server.py"],
    "yahoo-finance": ["stdio", "npx", "-y", "@executeautomation/yahoo-finance-mcp"],
    "tinkoff": ["stdio", "python3", "/root/.hermes/tinkoff-mcp/server.py"],
}

def check_tcp(host, port, timeout=3):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except: return False

def check_stdio(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
                         capture_output=True, text=True, timeout=timeout)
        return '"jsonrpc":"2.0"' in r.stdout
    except: return False

results = {"healthy": 0, "dead": 0, "disabled": 0}
for name, cfg in MCP_SERVERS.items():
    status = "❌"
    if cfg[0] == "stdio":
        if check_stdio(cfg[1:], 5):
            status = "✅"
            results["healthy"] += 1
        else:
            results["dead"] += 1
    print(f"  {status} {name}")

print(f"\nMCP Health: {results['healthy']}✅ {results['dead']}❌ disabled")
