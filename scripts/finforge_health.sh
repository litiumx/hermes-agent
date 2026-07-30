#!/bin/bash
SERVICE="finforge"
TOKEN="8991001886:AAEB2pGKb4Zdmmey-2RXwE9DXzD8RRr-9vA"
if ! systemctl is-active --quiet $SERVICE; then
    systemctl restart $SERVICE 2>/dev/null
    echo "FinForge restarted at $(date)" >> /root/.hermes/logs/finforge.log
fi
# Проверка API TG
curl -s "https://api.telegram.org/bot$TOKEN/getMe" | grep -q '"ok":true' || echo "TG API DOWN at $(date)" >> /root/.hermes/logs/finforge.log
