#!/bin/bash
# Disk Guardian — мониторинг и автоочистка диска
# Hermes Agent · Cron: каждые 30 минут

DISK_PCT=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
LOG="/tmp/disk_guardian.log"

echo "[$(date)] Disk: ${DISK_PCT}%" >> "$LOG"

if [ "$DISK_PCT" -le 80 ]; then exit 0; fi

# Жёлтая зона
if [ "$DISK_PCT" -lt 90 ]; then
    echo "⚠️  WARNING: Disk at ${DISK_PCT}%" | tee -a "$LOG"
    exit 0
fi

# Оранжевая зона
if [ "$DISK_PCT" -lt 95 ]; then
    echo "🟠 ACTION: Light cleanup at ${DISK_PCT}%" | tee -a "$LOG"
    docker buildx prune -f 2>&1 | tail -1 >> "$LOG"
    docker image prune -f > /dev/null 2>&1
    apt-get clean -y 2>/dev/null
    DISK_NOW=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
    echo "  Disk after: ${DISK_NOW}%" | tee -a "$LOG"
    exit 0
fi

# Красная зона
echo "🔴 ALARM: Critical disk at ${DISK_PCT}% — aggressive cleanup" | tee -a "$LOG"
docker buildx prune -f > /dev/null 2>&1
docker image prune -a -f 2>&1 | grep "Total" | head -1 >> "$LOG"
docker container prune -f > /dev/null 2>&1
apt-get autoclean -y 2>/dev/null
apt-get autoremove -y 2>/dev/null
journalctl --vacuum-size=100M 2>/dev/null
DISK_NOW=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
echo "  After cleanup: ${DISK_NOW}%" | tee -a "$LOG"

if [ "$DISK_NOW" -gt 97 ]; then
    echo "  🔴 CRITICAL: Need manual intervention!" | tee -a "$LOG"
fi
