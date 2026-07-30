#!/bin/bash
# Weekly Hermes Agent auto-update
# Runs: hermes update --check → if available → hermes update -y

LOG="/tmp/hermes-update-$(date +%Y%m%d).log"

echo "[$(date)] Checking for Hermes updates..." | tee -a "$LOG"

# Check if update available
UPDATE_CHECK=$(timeout 30 hermes update --check 2>&1)
echo "$UPDATE_CHECK" >> "$LOG"

if echo "$UPDATE_CHECK" | grep -qi "update.*available\|new.*version\|behind"; then
    echo "[$(date)] Update available! Running hermes update -y..." | tee -a "$LOG"
    timeout 120 hermes update -y 2>&1 | tee -a "$LOG"
    echo "[$(date)] Update complete." | tee -a "$LOG"
else
    echo "[$(date)] Already up to date." | tee -a "$LOG"
fi

# Keep only last 4 logs
ls -t /tmp/hermes-update-*.log 2>/dev/null | tail -n +5 | xargs rm -f 2>/dev/null

echo "[$(date)] Done." | tee -a "$LOG"
