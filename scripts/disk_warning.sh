#!/bin/bash
USED=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$USED" -gt 89 ]; then
  echo "⚠️ Диск $USED% — срочно чистить!"
fi
