# SPECIFICATION & MATHEMATICAL CONTRACT

## 1. Entities
- VPS: server (78.17.67.169), disk=/dev/vda1 59G, RAM=7.8G, CPU=4 cores
- MemoryDB: /root/.hermes/memory_store.db, facts=31
- MCP: router, n8n, paperclip, notebooklm, travelpayouts, yahoo-finance, tinkoff, browser

## 2. Invariants
- disk_used_pct < 90% ⇒ status=green
- disk_used_pct > 85% ⇒ warning
- MCP_servers_enabled = 8
- gateway_status = active
- facts_count ≥ 30

## 3. Boundaries
- disk_used_pct = 88% > 85% ⇒ warning
- disk_free = 7G = OK (> 5G threshold)
- MCP_disabled = 1 (sequential-thinking)

## 4. Layers
- [disk] → df -h
- [gateway] → systemctl is-active hermes-gateway
- [MCP] → hermes mcp list
- [memory] → python3 /root/.hermes/memory_manager.py count
