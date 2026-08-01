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
