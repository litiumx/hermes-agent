1|# Memory
2|
3|## Система
4|- Сервер: AgencyForge (AdminVPS, Ubuntu 22.04)
5|- IP: 78.17.67.169
6|- Hermes v0.19.0, DeepSeek V4 Flash (осн.), GitHub Models (4 шт, бэкап)
7|- Dashboard: :9119 (логин hermes)
8|- Tailscale: agencyforge-1 (100.114.47.18) ←→ home-pc (100.105.159.88)
9|- Диск: 85% заполнен (9G свободно) — пора чистить
10|
11|## Проекты
12|- Paperclip — финотдел (Atomic Finance), multi-company (AGE v1=f0b8eb0f MCP-scoped, v2=504616f4 DB-only)
13|- Tinkoff Delta Bot — трейдинг (Python)
14|- domu-lesa.ru — веб-проект
15|- Atomic Finance — MCP + CFO pipeline (marketplace finance, налоги, ДДС)
16|- Hermes Agent — основной ассистент
17|
18|## MCP Подключения (12 серверов)
19|- atomic_finance — CFO пайплайн ✅
20|- paperclip — 41 инструмент ✅
21|- notebooklm — 30+ инструментов ✅
22|- travelpayouts — 11 инструментов ✅
23|- yahoo-finance — финансы ✅
24|- tinkoff — Тинькофф ✅
25|- router — Keenetic роутер ✅
26|- browser — Browser automation (Agent360) ✅
27|- cbr — ЦБ РФ курсы ✅
28|- computer — Computer Use ✅
29|- n8n — n8n automation ✅
30|- wikivibe — Wikivibe skills/search ✅
31|
32|## Windows (home-pc)
33|- Tailscale: 100.105.159.88, ping ~18ms
34|- SSH: ✅ aleksey@100.105.159.88 (через Tailscale)
35|- Browser MCP через Agent360 (вместо Playwright bridge)
36|- RDP: :3389
37|- CUA-driver MCP через SSH туннель :19877
38|
39|## Безопасность
40|- fail2ban: активен (46+ забаненных)
41|- UFW: 16 allow, 12 deny (только 22,8642-44,9119,9443,4123)
42|- Approvals: smart mode
43|- Хуки: audit-log, block-rm, protect-files
44|- Бекап: supervisor-скрипт (каждые 30 мин)
45|
46|## RAG / Память
47|- sentence-transformers: MiniLM 384-dim ✅
48|- zvec: индекс в /root/.hermes/memory-rag
49|- context_engine: включён
50|- memory_store.db: 80KБ
51|- Memory cron: autosave (каждые 6ч), decay (4:00), graph (4:15), consolidate (4:30)
52|
53|## Cron
54|- Supervisor: */30 * * * * (healthcheck + issue tracking)
55|- Memory Autosave: 0 */6 * * *
56|- Memory Decay: 0 4 * * *
57|- Memory Graph: 15 4 * * *
58|- Memory Consolidate: 30 4 * * *
59|- Atomic Finance Cleanup: */30 * * * * + @reboot
60|
61|## Docker
62|- netdata — мониторинг
63|- gw-proxy — nginx proxy
64|- docker-server-1 — Paperclip/Atomic Finance сервер
65|- docker-db-1 — PostgreSQL (Paperclip, Atomic Finance)
66|- claude-code-router-ccr-1 — Claude Code роутер
67|- hermes-e15b137b — Hermes sandbox
68|
69|## Стиль общения
70|- Caveman mode, кратко, по делу
71|- Русский язык
72|- Дни недели: Пн/Вт/Ср/Чт/Пт/Сб/Вс
73|- Авиакомпании: SU=☭, DP=🏳, FV=🇷🇺, U6=✈
74|- API ключи ТОЛЬКО в .env (600), никогда в LLM
75|
76|## Opus 4.8 Upgrade (2026-07-22)
77|- Effort Control: /effort max|high|fast|auto
78|- Plan Pushback Validator: risk_score >0.7 = reject
79|- Post-Execution Self-Checker: validates every tool call
80|- Goal Clarify Analyzer: max 3 clarifying questions
81|- Adversarial Review: 2 agents + reviewer
82|- Mid-Conversation: fork strategy + user inject
83|- Docker Sandbox: hermes-sandbox:latest, max 5 containers
84|- Security: 5 hooks, 20+ blocked patterns
85|- Cache: 96.0% hit rate, K+ saved
86|- Browser: Agent360 MCP (вместо Playwright)
87|- Backup: /root/.hermes.backup-20260722-0228
88|
89|## Hermes scripts
90|- proactive_scan.py — системный скан при старте сессии
91|- flash_router.py — авто-выбор модели/swarm
92|- self_improve.py — анализ ошибок, патч SOUL.md
93|- supervisor.py — healthcheck каждые 30 мин
94|- backup_to_github.py, disk_guardian.sh, kill_switch_check.sh
95|- auto_dream.py — ночная чистка памяти
96|- mcp_healthcheck.py — мониторинг MCP серверов
97|
98|## Известные процедуры
99|- Windows shell: .ps1 → scp → execute (не heredoc)
100|- Swarm: ручной /swarm N (авто-триггер отключён)
101|- Shopping Billing ZIP: CreditClaw — Casey→85009, Alex→10006, Maria→60607
102|- Browser DOM: через browser_console JS (React: Object.defineProperty + dispatchEvent)
103|- DeepSeek: 1M ctx, Flash think=on, Pro $0.435/$0.87 Flash $0.14/$0.28
104|