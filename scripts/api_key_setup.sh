#!/bin/bash
export PATH="/usr/local/lib/hermes-agent/.venv/bin:$PATH"

echo "HERMES_API_KEY=I7SO5UqL9zPxMNL9j5jpblz9K2yOj5un4bTaWNdUrpg" >> /root/.hermes/gateway.env

hermes config set api_server.api_key "I7SO5UqL9zPxMNL9j5jpblz9K2yOj5un4bTaWNdUrpg" 2>&1

systemctl --user restart hermes-gateway 2>&1
echo "API key set + gateway restarted"
