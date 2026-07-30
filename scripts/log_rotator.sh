#!/bin/bash
# Log Rotator — чистит старые логи, сжимает большие
LOG_DIR="/root/.hermes/logs"
DAYS_KEEP=14
MAX_LOG_SIZE_MB=50

# Удалить логи старше 14 дней
find "$LOG_DIR" -name "*.log" -type f -mtime +$DAYS_KEEP -delete 2>/dev/null
find "$LOG_DIR" -name "*.log.*" -type f -mtime +$DAYS_KEEP -delete 2>/dev/null

# Сжать большие логи (>50MB)
find "$LOG_DIR" -name "*.log" -type f -size +${MAX_LOG_SIZE_MB}M -exec gzip -f {} \; 2>/dev/null

echo "Log rotator done: $(date)"
