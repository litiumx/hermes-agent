# Проекты: факты

Дата сбора: 2026-07-19 22:31 MSK

## Факты (cat|тег|факт)

project|hermes-agent|Установлен v0.18.2, работает gateway (PID 74712/95248), основной провайдер DeepSeek V4 Flash
project|hermes-agent|Слушает 3 порта: dashboard=9119, api-server=8642, webhook=8644 (все на 0.0.0.0)
project|hermes-agent|Включено 8 MCP-серверов: paperclip, notebooklm, travelpayouts, router, browser, sequential-thinking, yahoo-finance, tinkoff
project|hermes-agent|Платформа Telegram настроена, терминал — локальный, fallback модель — GitHub Models GPT-4o-mini
project|paperclip|MCP сервер работает через stdio (Node.js /opt/paperclip/packages/mcp-server/), запущен в 2х экземплярах (dashboard + gateway)
project|paperclip|API запущен в Docker (docker-server-1) на порту 3100, БД — PostgreSQL (docker-db-1) на порту 5432, внутренняя сеть 172.18.0.0
project|paperclip|Подключается через bash-враппер /usr/local/bin/paperclip-mcp.sh, API_URL=http://localhost:3100, Company ID: 92f57252-...ca5b
project|router-mcp|Python MCP-сервер (routerich_mcp_server.py) — подключается к роутеру OpenWRT через SSH по Tailscale (IP: 100.102.57.232)
project|router-mcp|Предоставляет 12+ инструментов: статус, firewall, DHCP, WiFi, VPN, adblock, логи, пинг, перезапуск сервисов
project|router-mcp|Не имеет собственного порта — работает как stdio MCP, все команды через SSH к роутеру
project|browser-mcp|Подключается через socat к TCP:100.69.1.232:3099 — Windows-машина в Tailscale, где запущен @agent360/browser-mcp
project|browser-mcp|Использует прокси (browser-proxy.py) для переименования browser_* → browse_* во избежание конфликта с core tools
project|browser-mcp|Альтернативный канал — SSH туннель через ключ ~/.ssh/tailscale_windows к пользователю aleksey@100.69.1.232
project|all-mcp|Все MCP работают в режиме enabled, каждый обёрнут в mcp_stdio_watchdog.py для автоматического перезапуска
