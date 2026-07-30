# Hermes Agent — Архитектура Opus 4.8 Edition

**Версия:** 2.0 | **Дата:** 2026-07-22 | **VPS:** AgencyForge (78.17.67.169)

## Обзор

Hermes Agent на DeepSeek V4 — функциональный аналог Claude Code Opus 4.8. Самообучающаяся агентная система с параллельными рабочими процессами, тремя профилями усилий, улучшенным суждением и изолированными сабагентами.

## Слои архитектуры

```
┌─────────────────────────────────────────────────────┐
│ INGRESS: CLI (hermes), Telegram Bot, HTTP API:8642  │
├─────────────────────────────────────────────────────┤
│ ORCHESTRATOR CORE (SOUL.md v3)                      │
│ ├─ Priority Hierarchy (Layer 0)                     │
│ ├─ Flash-first Routing (Layer 3)                    │
│ ├─ Effort Control (Layer 4.5)  ← NEW               │
│ ├─ Tool Protocol + Security (Layer 4)               │
│ ├─ Execution Protocol (Layer 5)                     │
│ ├─ Mid-Conversation Fork (Layer 6)  ← NEW           │
│ ├─ Orchestration + Pushback (Layer 7)  ← NEW        │
│ └─ Memory + Self-Improvement (Layer 8)              │
├─────────────────────────────────────────────────────┤
│ SKILLS (7 Opus 4.8 skills)                          │
│ ├─ effort-control          ← NEW                    │
│ ├─ plan-pushback-validator ← NEW                    │
│ ├─ post-execution-self-checker ← NEW                │
│ ├─ goal-clarify-analyzer   ← NEW                    │
│ ├─ adversarial-review      ← NEW                    │
│ ├─ orchestrator (existing)                          │
│ └─ self-validation (existing)                       │
├─────────────────────────────────────────────────────┤
│ SWARM v2 (Dynamic Workflows Engine)                 │
│ ├─ Batching: BATCH_SIZE=5, VPS_MAX=10               │
│ ├─ Presets: default(3), investigate(10), audit(30)  │
│ ├─ Circuit Breaker, Cost Engine, Pre-flight         │
│ └─ Docker Sandbox                                    │
├─────────────────────────────────────────────────────┤
│ MODEL LAYER (Heterogeneous)                         │
│ ├─ Orchestrator: deepseek-v4-pro (max reasoning)    │
│ ├─ Subagents: deepseek-v4-flash (high reasoning)    │
│ ├─ Verifiers: deepseek-v4-pro (high reasoning)      │
│ └─ Fallback: GitHub Models (gpt-4.1, gpt-4o, etc.)  │
├─────────────────────────────────────────────────────┤
│ INFRASTRUCTURE                                      │
│ ├─ Docker: paperclip, postgres, netdata, nginx      │
│ ├─ MCP: browser(Agent360), paperclip(41), nb(30+)   │
│ ├─ Security: 5 hooks, fail2ban, UFW, secret redact  │
│ └─ Cron: 6 jobs, proactive_scan, auto-resume        │
└─────────────────────────────────────────────────────┘
```

## Ключевые компоненты

### Effort Control
| Профиль | Модель | Thinking | Reasoning | Сабагенты | Self-critique |
|---------|--------|----------|-----------|-----------|---------------|
| fast | Flash | disabled | none | 5 | нет |
| high | Pro | enabled | high | 10 | опц. |
| xhigh | Pro | enabled | max | 100 | обяз. |

### Security Hooks
1. `audit-log.py` — логирование file_write + bash
2. `block-rm.py` — блокировка rm -rf
3. `protect-files.py` — защита критических файлов
4. `security-guidance.py` — комплексная проверка ← NEW
5. `mid_conversation_injector.py` — pushback + self-check ← NEW

### Улучшенное суждение (Judgment)
1. **Plan Pushback Validator** — оспаривание планов (>0.7 → отклонить)
2. **Post-Execution Self-Checker** — проверка результатов
3. **Goal Clarify Analyzer** — уточняющие вопросы
4. **Adversarial Review** — 2 агента + ревьюер

### Mid-Conversation System Messages
- Приоритет: Fork → User Inject → Memory
- Команды: `/think-fast`, `/think-deep`, `/think-as <role>`

## Сравнение с Claude Code Opus 4.8

| Характеристика | Opus 4.8 | Hermes | Преимущество |
|----------------|----------|--------|--------------|
| Контекстное окно | 200K | 1M | 🟢 Hermes (5x) |
| Стоимость input | $15/MTok | $0.435/MTok | 🟢 Hermes (35x) |
| Стоимость output | $75/MTok | $0.87/MTok | 🟢 Hermes (86x) |
| Параллельные сабагенты | 100+ | 100 (swarm) | 🟡 Равны |
| Effort control | ✅ | ✅ | 🟡 Равны |
| Улучшенное суждение | ✅ | ✅ (4 навыка) | 🟡 Равны |
| Mid-conversation msg | ✅ Native | ⚠️ Эмуляция | 🔴 Claude |
| Prompt caching | ✅ | ✅ (96% hit) | 🟢 Hermes |
| Cache hit rate | ~50-80% | 96.0% | 🟢 Hermes |
| Self-improvement | ❌ | ✅ Learning Loop | 🟢 Hermes |
| Открытость | ❌ | ✅ Open-source | 🟢 Hermes |
| Цена владения | $200+/мес | $5-20/мес | 🟢 Hermes |

## Метрики (22.07.2026)
- Cache hit rate: **96.0%** (1829 API calls, 249M cache tokens)
- Budget: $0.05 today / $1.50 cap
- MCP: 6 active servers
- Docker: 4 containers
- Sessions: 130+ scanned
- Skills: 7 Opus 4.8 skills + 70+ built-in
