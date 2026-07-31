# Memory
_Обновлено: 2026-07-31 (cron memory-check)_

## Система
- Сервер: AgencyForge (AdminVPS, Ubuntu 22.04)
- IP: 78.17.67.169
- Hermes v0.19.0 (2026.7.20), DeepSeek V4 Flash (осн.), GitHub Models (4 шт, бэкап)
- Dashboard: :9119 (логин hermes)
- Tailscale: VPS agencyforge **100.108.199.226** ←→ home-pc **100.105.159.88**; роутер Keenetic 100.102.57.232 (SSH root, ключ ~/.ssh/hermes_router)
- Диск: **88% (7.2G свободно)** — критично, нужна чистка (бэкап .hermes.backup 1.2G, docker 8.8G, /var/log 1.7G)
- Gateway: работают 3 процесса (старый PID 2577566 с 27.07; зомби node PID 167878 держит порт 3456 с 21.07) — нужен ручной kill + systemctl restart hermes-proxy

## Проекты
- **FinForge** — AI-продавец ботов для бизнеса (Telegram): бот @FIN_FORGE_BOT (v4, 326 строк, systemd finforge, finforge.db); цены 4 900 / 9 900 / от 29 900 ₽/мес; ROI-калькулятор https://78.17.67.169:4123/artifacts/finforge-roi.html (WebApp, требует HTTPS) + http://78.17.67.169:4222/ (браузер); sales_scripts.py, leadgen_plan.py, finforge_health.sh (timer 2 мин); план /root/.hermes/plans/ai-earn-plan.md; путь: бот → 3 клиента ($600 MRR) → SaaS ($3000 MRR); лидген: Avito + TG-чаты (топ-8 найдены) — за пользователем
- Paperclip — финотдел (Atomic Finance), multi-company (AGE v1=f0b8eb0f MCP-scoped, v2=504616f4 DB-only); MCP периодически штормит рестартами
- Tinkoff Delta Bot — трейдинг (Python)
- domu-lesa.ru — веб-проект
- Atomic Finance — MCP + CFO pipeline (marketplace finance, налоги, ДДС)
- Hermes Agent — основной ассистент

## MCP Подключения (12 серверов)
- atomic_finance — CFO пайплайн ✅
- paperclip — 41 инструмент ✅ (штормит рестартами при старте кронов)
- notebooklm — 30+ инструментов ✅
- travelpayouts — 11 инструментов ✅
- yahoo-finance — финансы ✅
- tinkoff — Тинькофф ✅
- router — Keenetic роутер ✅
- browser — Browser automation (Agent360) ✅
- cbr — ЦБ РФ курсы ✅
- computer — Computer Use ✅
- n8n — n8n automation (через wrapper /root/.hermes/n8n-mcp.py, URL finforge.app.n8n.cloud/mcp-server/http, 33 инструмента) ✅
- wikivibe — Wikivibe skills/search ✅
- ⚠️ browser-bridge.service (Windows) — залипал в auto-restart, когда Windows офлайн; при health-check смотреть `systemctl list-units --failed`

## Windows (home-pc)
- Tailscale: 100.105.159.88, SSH ✅ aleksey@ (через Tailscale)
- Browser MCP через Agent360 (browser-mcp pm2 :3099, socat STDIO TCP)
- RDP: :3389
- CUA-driver MCP через SSH туннель :19877
- Shell: .ps1 → scp → `cmd /c powershell -ExecutionPolicy Bypass -File script.ps1` (НЕ heredoc)

## Безопасность
- fail2ban: sshd + nginx jails (nginx-http-auth, nginx-botsearch)
- UFW: 16 allow, 12 deny (22, 8642-44, 9119, 9443, 4123, 4222)
- iptables DOCKER-USER DROP извне (PostgreSQL/Paperclip закрыты от мира)
- authorized_keys: 6 ключей (было 14)
- mkcert CA: /etc/ssl/certs/mkcert.crt (до 2028-10-21), nginx paperclip на mkcert
- Approvals: smart mode; хуки: audit-log, block-rm, protect-files
- Бекап: supervisor-скрипт (каждые 30 мин) + GitHub Daily Backup (cron 0 3)

## RAG / Память
- memory_store.db: **36 фактов** (пересоздана после потери 22.07 при апгрейде — memory_manager.py восстановлен)
- zvec: индекс в /root/.hermes/memory-rag; FTS5; context_engine: включён
- auto_dream.py — nightly (cron 0 3): Prune → Merge → Refresh
- memory tool лимит ~2.2K chars; большие объёмы — прямой SQLite через memory_manager.py

## Cron (25 задач)
- Новые: AGI Coding Agent (0 */6, пишет код), Zombie Killer (*/3), Session Context Saver (*/30), Proactive Scan (0 */4), Auto Resume (*/2), Hermes Self-Reflection Daily (0 23)
- Supervisor: */30 (healthcheck); Memory Autosave: 0 */6; EOD Error Analysis: 0 19
- ❌ В ошибке: SaaS Budget Weekly Health Check, Hermes Weekly Review (нужен разбор)
- FinForge Daily Plan — УДАЛЁН 31.07 (скрипт не существовал, спамил ошибками)
- ⚠️ Cron-ограничения (песочница): blocked — rm -rf, systemctl kill, gateway restart, write_file/patch config.yaml, execute_code, memory tool; works — hermes mcp remove, write_file не-config, terminal без rm, session_search. Если проблема persist >24ч — эскалация в TG сразу (не ждать 23:00)

## Docker
- netdata — мониторинг (зомби-дети — чистит zombie-killer)
- gw-proxy — nginx proxy
- docker-server-1 — Paperclip/Atomic Finance сервер
- docker-db-1 — PostgreSQL (Paperclip, Atomic Finance)
- claude-code-router-ccr-1 — Claude Code роутер
- hermes-e15b137b — Hermes sandbox

## AGI Self-Improvement (25–31.07)
- AGI Coding Agent cron: пишет/тестирует/коммитит код бесплатными GitHub-моделями (gpt-4o-mini осн., gpt-4o, o3-mini), вход — SELF_IMPROVE_*.md, коммиты → litiumx/hermes-agent
- Скрипты (в /root/.hermes/scripts/, префикс agi_): agi_context_store.py (SQLite WAL), agi_session_bridge.py (SIGUSR1 auto-save), agi_error_pattern_learner.py (ML-предсказатель), agi_curious_agent.py (DuckDuckGo), agi_self_directed_queue.py
- **auto_resume.py: JSON PID fix (29.07)** — root cause 3-дневного gateway respawn storm (int(JSON) → ValueError → ложный рестарт)

## Стиль общения
- Caveman mode, кратко, по делу; русский язык
- НЕ выводить в чат [SESSION STATE], [router:], [ДЕКОМПОЗИЦИЯ] — тех. блоки только в thinking
- Код/боты/интеграции → ТОЛЬКО Claude Code CLI (`echo '...' | claude -`; `--prompt` не работает)
- ВСЕ ссылки/юзернеймы проверять curl перед отправкой; клиенту НИКОГДА лид/score/админ
- Дни недели: Пн/Вт/Ср/Чт/Пт/Сб/Вс; API ключи ТОЛЬКО в .env (600)
- Авиакомпании: SU=☭, DP=🏳, FV=🇷🇺, U6=✈

## Известные процедуры
- Swarm: только ручной /swarm N; HARD RULE: перед ЛЮБЫМ delegate_task — `python3 /root/.hermes/agent/swarm.py get_size`, запускать ровно сколько в config (default 3, max_cost $1.0)
- Gateway рестарт ТОЛЬКО снаружи (Dashboard :9119 / systemctl) — изнутри заблокирован (защита от respawn storm)
- Shopping Billing ZIP (CreditClaw): Casey→85009, Alex→10006, Maria→60607, Taylor→94117, Logan→98101, Emerson→77001, Robin P. Anderson→10002; bulk-fill + verify перед Pay
- Browser DOM: через browser_console JS (React: Object.defineProperty + dispatchEvent); IIFE для const
- DeepSeek: 1M ctx, Flash think=on, Pro $0.435/$0.87 Flash $0.14/$0.28; prefix cache: -$0.0036/$0.0028
- n8n MCP: одно сообщение за POST, Accept: application/json + text/event-stream, токен из .env через wrapper (не `hermes mcp add --auth header` — getpass не работает через pipe)
- Hermes venv: /usr/local/lib/hermes-agent/.venv/bin/python3 (3.11) — pip ставить туда
- config.yaml: НЕ трогать yaml.dump (ломает порядок/правки); MCP — через `hermes mcp add/remove`
