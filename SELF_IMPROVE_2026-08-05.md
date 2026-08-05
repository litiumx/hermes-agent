# 🧬 Само-улучшение — 05.08.2026 (AGI cron)

## Что сделано
- Новый `scripts/agi_config_guard.py` — реальная валидация конфигов:
  - YAML-парсинг config.yaml (PyYAML, не regex)
  - JSON-парсинг всех data/*.json, session/*.json, *.json в корне (кроме .git/worktrees/cache, >1MB)
  - exit 1 при битых, `--json` для интеграций, `--write` → error_patterns.json (config_corrupt_real)
- Закрыта дыра: config_corrupt детектился только по regex в логах → ложные риски в очереди.
  Проверка 119 реальных файлов: ВСЕ валидны — risk 'config_corrupt (rising)' в task_queue был шумом.

## Тесты
- compile() — OK
- 119 файлов проверено, 0 битых
- Тест на битых: bad.json (line 2: Expecting value) + bad.yaml (flow sequence) — оба пойманы, PASS

## Очередь
- risk-задачи mcp_crash/auth_failure/request_timeout/... стабильны (не rising) — не трогаем
- config_corrupt: подтверждён как regex-шум, теперь есть реальный гард
