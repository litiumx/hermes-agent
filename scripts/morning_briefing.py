#!/usr/bin/env python3
"""
Утренний брифинг Гермеса — 6:00 MSK (3:00 UTC).
Проверяет всё на VPS, шлёт сводку в Telegram. 0 токенов API.
"""
import subprocess, json, sys, os, time
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "353133098")

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", 1

def telegram_send(msg):
    if not TELEGRAM_BOT_TOKEN:
        print("[NO TOKEN]", msg)
        return
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        print(f"[TG FAIL] {e}")

class Check:
    def __init__(self, name, cmd, ok_pattern=None, fail_pattern=None, critical=False):
        self.name = name
        self.cmd = cmd
        self.ok_pattern = ok_pattern
        self.fail_pattern = fail_pattern
        self.critical = critical

    def run(self):
        out, err, code = run(self.cmd)
        if self.ok_pattern and self.ok_pattern in (out + err):
            return "✅", out[:200]
        if code != 0 and not self.fail_pattern:
            return "🔴" if self.critical else "🟡", err[:200] or out[:200]
        if self.fail_pattern and self.fail_pattern in (out + err):
            return "🔴" if self.critical else "🟡", (err or out)[:200]
        return "✅", out[:200]

CHECKS = [
    Check("Docker", "docker ps --format '{{.Names}}' | wc -l", critical=True),
    Check("Gateway", "systemctl is-active hermes-gateway 2>&1", ok_pattern="active", critical=True),
    Check("Netdata", "curl -s -o /dev/null -w '%{http_code}' http://localhost:19999 2>&1", ok_pattern="200", critical=False),
    Check("Nginx", "curl -s -o /dev/null -w '%{http_code}' http://localhost:80 2>&1", ok_pattern="200", critical=False),
    Check("Disk", "df -h / | tail -1 | awk '{print $5}'", critical=True),
    Check("RAM", "free -m | awk 'NR==2{printf \"%.0f%%\", $3*100/$2}'", critical=True),
    Check("CPU", "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1", critical=False),
    Check("UFW", "ufw status | head -1", ok_pattern="active", critical=True),
    Check("fail2ban", "fail2ban-client status sshd 2>&1 | grep 'Currently banned'", critical=False),
    Check("Tailscale", "tailscale status --json 2>&1 | python3 -c 'import sys,json; s=json.load(sys.stdin); print(s.get(\"Self\",{}).get(\"Online\",\"unknown\"))'", ok_pattern="true", critical=True),
    Check("Backup", "ls -t /root/.hermes/disk-cleanup/ 2>/dev/null | head -1", critical=False),
]

def get_docker_containers():
    out, _, _ = run("docker ps --format '{{.Names}} {{.Status}}'")
    return out.split("\n") if out else []

def get_recent_errors():
    """Последние ошибки из логов (последние 24ч)"""
    out, _, _ = run("grep -c ERROR /root/.hermes/logs/*.log 2>/dev/null || echo 0")
    return out

def get_cache_stats():
    """Примерный cache hit rate из логов gateway"""
    out, _, _ = run("grep -c 'cache_hit' /root/.hermes/gateway-starts.log 2>/dev/null || echo 0")
    return out

def main():
    now = datetime.now(MSK)
    lines = [f"🌅 <b>Гермес — утренний брифинг</b>", f"{now.strftime('%d.%m.%Y %H:%M MSK')}", ""]

    # Сервисы
    ok, warn, fail = 0, 0, 0
    for c in CHECKS:
        status, detail = c.run()
        icon = status
        if "✅" in status: ok += 1
        elif "🔴" in status: fail += 1
        else: warn += 1
        lines.append(f"{icon} <b>{c.name}</b>: {detail}")

    # Сводка
    lines.append("")
    lines.append(f"✅ {ok} | 🟡 {warn} | 🔴 {fail}")

    # Docker
    lines.append("")
    lines.append("<b>📦 Docker контейнеры:</b>")
    for c in get_docker_containers():
        if c.strip():
            lines.append(f"  • {c.strip()}")

    # Бюджет API (сегодня)
    out, _, _ = run("python3 -c \"from context.token_tracker import get_today_usage; print(get_today_usage())\" 2>/dev/null || echo 'N/A'")
    lines.append("")
    lines.append(f"<b>💰 API сегодня:</b> {out}")

    msg = "\n".join(lines)
    print(msg)
    telegram_send(msg)

if __name__ == "__main__":
    main()
