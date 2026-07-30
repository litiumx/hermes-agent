#!/usr/bin/env python3
"""Proactive scan — проверка системы при старте сессии.
Вывод: 🔴⚠️🟢 статус + топ проблем + рекомендации."""
import subprocess, json, socket, os, sys

# Session Bridge — восстановление контекста предыдущей сессии
try:
    from session_bridge import get_last_session_summary
    summary = get_last_session_summary()
    if summary and "нет данных" not in summary.lower():
        print(summary)
        print()
except Exception:
    pass

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except: return ""

def check_disk():
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except: return ""

def scan():
    issues = []
    OK = "🟢"
    WARN = "⚠️"
    CRIT = "🔴"

    # 1. Диск
    df = run(["df", "-h", "/"])
    used_pct = 0
    free_gb = 0
    for line in df.split("\n"):
        if line.startswith("/"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    used_pct = int(parts[4].rstrip("%"))
                    free_str = parts[3]
                    # Normalize free
                    if "G" in free_str:
                        free_gb = float(free_str.replace("G", ""))
                    elif "T" in free_str:
                        free_gb = float(free_str.replace("T", "")) * 1024
                    elif "M" in free_str:
                        free_gb = float(free_str.replace("M", "")) / 1024
                except: pass

    if used_pct >= 95:
        issues.append((CRIT, f"Disk {used_pct}% — КРИТИЧЕСКИ! {free_gb:.0f}G свободно"))
    elif used_pct >= 85:
        issues.append((WARN, f"Disk {used_pct}% — {free_gb:.0f}G свободно. Пора чистить"))
    else:
        issues.append((OK, f"Disk {used_pct}% — {free_gb:.0f}G свободно"))

    # 2. Gateway статус
    gw = run(["systemctl", "is-active", "hermes-gateway"])
    if gw != "active":
        issues.append((CRIT, f"Gateway: {gw} — НЕ РАБОТАЕТ!"))
    else:
        issues.append((OK, "Gateway активен"))

    # 3. MCP статус
    mcp_out = run(["hermes", "mcp", "list"], timeout=15)
    mcp_total = 0
    mcp_enabled = 0
    mcp_failed = []
    for line in mcp_out.split("\n"):
        if "enabled" in line:
            mcp_enabled += 1
            mcp_total += 1
        elif "error" in line.lower() or "fail" in line.lower() or "401" in line or "500" in line:
            name = line.split()[0] if line.split() else "?"
            mcp_failed.append(name)
            mcp_total += 1
        elif line.strip() and not line.startswith(" ") and "/" not in line:
            mcp_total += 1

    if mcp_failed:
        issues.append((WARN, f"MCP: {len(mcp_failed)}/{mcp_total} не отвечают ({', '.join(mcp_failed[:3])})"))
    elif mcp_total > 0:
        issues.append((OK, f"MCP: {mcp_enabled}/{mcp_total} работают"))

    # 4. Память
    mem_out = run(["python3", "/root/.hermes/memory_manager.py", "count"], timeout=5)
    fact_count = 0
    if ":" in mem_out:
        try:
            fact_count = int(mem_out.split(":")[-1].strip())
        except: pass
    if fact_count > 0:
        issues.append((OK, f"Memory: {fact_count} фактов"))
    else:
        issues.append((WARN, "Memory: пусто или недоступна"))

    # 5. Ошибки в логах (последние)
    log_errors = run(["grep", "-c", "ERROR\\|CRITICAL\\|Traceback", "/root/.hermes/logs/gateway.log"], timeout=5)
    try:
        err_count = int(log_errors)
        if err_count > 10:
            issues.append((WARN, f"Gateway лог: {err_count} ошибок за сегодня"))
        else:
            issues.append((OK, f"Gateway лог: {err_count} ошибок"))
    except:
        pass

    return issues

def main():
    issues = scan()
    print("🧠 Системный скан\n")
    has_crit = any(i[0] == "🔴" for i in issues)
    has_warn = any(i[0] == "⚠️" for i in issues)

    for icon, text in issues:
        print(f"  {icon}  {text}")

    print()
    if has_crit:
        crit = [t for i, t in issues if i == "🔴"]
        print(f"🔴 {len(crit)} проблемы. Самое срочное: {crit[0]}")
        print("   Исправляю?")
    elif has_warn:
        print("⚠️ Есть предупреждения. Что делаем?")
    else:
        print("✅ Система в норме. Что делаем?")

if __name__ == "__main__":
    main()
