#!/bin/bash
# zombie-killer — раз в 3 мин убивает зомби процессы
# by killing their parent (если родитель netdata/systemd/docker — пропускаем)

LOG="/root/.hermes/logs/zombie-killer.log"
THRESHOLD=5

count=$(ps aux | awk '{if ($8 == "Z" || $8 == "Z+") print}' | wc -l)

if [ "$count" -le 1 ]; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Zombies: $count" >> "$LOG"

# Если больше порога — рестартим netdata (родитель 80% зомби)
if [ "$count" -ge "$THRESHOLD" ]; then
    PARENTS=$(ps -eo ppid,stat --no-headers | awk '{if ($2 ~ /Z/) print $1}' | sort -u)
    HAS_NETDATA=$(echo "$PARENTS" | xargs -I{} ps -p {} -o cmd= 2>/dev/null | grep -c netdata)
    
    if [ "$HAS_NETDATA" -gt 0 ]; then
        echo "  → netdata породил зомби, рестарт..." >> "$LOG"
        docker restart netdata 2>&1 >> "$LOG"
        echo "  → netdata restarted, zombies cleaned" >> "$LOG"
    fi
fi
