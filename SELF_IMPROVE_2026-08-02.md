# 🧬 Само-улучшение — 02.08.2026 (AGI cron)

## Сделано: error_pattern_learner.py v2
**Проблема:** лёрнер сканировал SUPERVISOR_LOG.md (только сводки «0 ошибок») и
несуществующие session_*.json → находил 0 паттернов, хотя errors.log полон ошибок.

**Фиксы:**
- Сканирует реальные логи: /root/.hermes/logs/{errors,agent,gateway,mcp-stderr}.log
  (mcp-stderr 12MB → хвост 2MB через _tail_text)
- Добавлены топ-паттерны: file_not_found, gateway_already_running (2220 совпадений!)
- Самообучение: повторяющиеся строки → learned_* паттерны в patterns.json,
  используются при следующих сканах (self-learning loop)
- Нормализация строк (срез уровня+[id]+модуля, check_* → check_FN) — шум схлопывается
- Фикс streaks: счётчик СКАНОВ (не сырых совпадений — росли бы бесконечно)
- Рекомендации для каждого паттерна (suggestion)

## Результат первого скана (02.08 21:05 MSK)
| Паттерн | Совпадений |
|---------|-----------|
| gateway_already_running | 2220 |
| mcp_crash | 1970 |
| auth_failure | 225 |
| request_timeout | 100 |
| process_killed | 49 |
| api_rate_limit | 28 |
| gateway_timeout | 21 |
| file_not_found | 7 |

Выучено 20 новых паттернов (GITHUB_TOKEN classic, aux clients unhealthy, registry check_fn).

## Реальные проблемы для внимания
- gateway_already_running 2220× — старый gateway не убивается перед рестартом
- mcp_crash 1970× — MCP keepalive/ClosedResourceError (notebooklm)
- auth_failure 225× — проверить API-ключи

---
*Сгенерировано AGI cron 5c8fb71aedfc*

---

## Сделано: agi_gateway_guard.py — защитник от двойных запусков gateway

**Проблема (из данных error_pattern_learner):** `gateway_already_running` 2220× в
logs/errors.log — повторные `hermes gateway run` БЕЗ `--replace` при живом инстансе
(31.07, PID 2577566). Дополнительно: `gateway.pid`/`gateway.lock` — JSON с ключом
`pid` внутри, НЕ plain PID (наивный `kill $(cat gateway.pid)` сломал бы).

**Что делает (scripts/agi_gateway_guard.py):**
- `status` — проверяет gateway.pid/gateway.lock: жив ли PID, совпадает ли argv
  (защита от PID reuse), битый JSON → проблема. Exit 1 = найдены проблемы.
- `scan` — все hermes-gateway процессы из /proc, сортировка по start, выявление дублей.
- `clean-stale` — удаляет stale lock/pid (PID мёртв или JSON битый), .bak перед удалением.
- `self-test` — синтетический тест 6 веток в temp (реальное состояние не трогает).

**Тесты:**
- Синтаксис: `compile()` OK
- Self-test: 6/6 веток (good/stale/broken/missing/alive/PID-reuse), exit 0
- `status` на реальной системе: gateway.pid OK (PID 2533595 жив, argv совпадает), exit 0
- `scan`: 1 процесс, дублей нет, exit 0

**Как использовать:** перед рестартом gateway — `python3 scripts/agi_gateway_guard.py status`;
при exit 1: `clean-stale` (если stale) или kill лишних процессов по `scan`.

**Следующие приоритеты:**
- mcp_crash 1970× (notebooklm ClosedResourceError) — keepalive-монитор для MCP
- auth_failure 225× — проверить ключи (GitHub token classic, DeepSeek)
- Добавить agi_gateway_guard в proactive_scan.py как раннюю проверку

---

## Сделано: agi_mcp_keepalive.py — keepalive-монитор MCP-серверов

**Проблема (из error_patterns):** mcp_crash — notebooklm keepalive failed
(ClosedResourceError), paperclip initial connection failed (TaskGroup).

**Что делает (scripts/agi_mcp_keepalive.py):**
- `scan` — парсит logs/errors.log за 24ч, агрегирует сбои по серверам
  (conn/keepalive/tool), состояния: ok / degraded / down / crash_loop
  (crash_loop = ≥3 сбоев за 10 мин), сохраняет в data/mcp_keepalive.json
- `status` — показывает сохранённое состояние
- `self-test` — синтетические логи, 4/4 веток, реальные файлы не трогает
- Exit 1 при down/crash_loop/degraded → готов для proactive_scan.py

**Первый скан (02.08 09:08 UTC):**
- 🔴 notebooklm down — 96 сбоев за 24ч (keepalive, ClosedResourceError),
  последний 09:02:59 — активная проблема
- 🟡 headroom degraded — 3 сбоя (parked after 3 attempts)

**Следующий шаг:** разобраться с notebooklm MCP (переустановка/restart),
добавить монитор в proactive_scan.py как раннюю проверку.
