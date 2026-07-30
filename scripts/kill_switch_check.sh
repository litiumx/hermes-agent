#!/bin/bash
# Kill Switch — проверка аварийного флага
# Если /tmp/hermes_kill_switch существует — выйти с ошибкой

if [ -f /tmp/hermes_kill_switch ]; then
    echo "🔴 KILL SWITCH ACTIVE: $(cat /tmp/hermes_kill_switch)"
    exit 1
fi
echo "✅ Kill switch: off"
