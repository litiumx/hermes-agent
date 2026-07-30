# SPEC — Hermes Agent Self-Audit

## 1. Entities
- SOUL.md: 10-layer system prompt (8.2KB)
- Tools: 100+ MCP + core Hermes tools
- Models: DeepSeek V4 Flash/Pro, GitHub Models
- Memory: 32 facts, 24 entities, zvec embeddings
- Stack: SOUL.md + MEMORY.md + USER.md + skills/ + memory_store.db + zvec-api
- Platforms: Telegram, terminal, MCP, cron, webhooks

## 2. Invariants
- reasoning_effort: max → deep-thinking only when needed
- tool_use: READ BEFORE WRITE, CHECK AFTER ACT
- delegation: sequential only (no parallel validator)
- memory: facts saved after verification, not in context

## 3. Boundaries
- SOUL.md 14.7KB (no more than 15KB)
- MCP stderr log: n8n, paperclip errors
- Gateway version: 0.18.2 (respawn storm bug)
- Model fallback: GitHub Models free tier (gpt-4o-mini limited)

## 4. Layers
- [prompt] → SOUL.md + MEMORY.md + USER.md + skills
- [tool] → delegate_task, terminal, MCP, cron
- [model] → DeepSeek V4 → GitHub Models fallback
- [memory] → memory_manager.py → SQLite + zvec
- [exec] → execution protocol → verify → save
