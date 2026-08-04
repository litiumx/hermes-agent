# 🧬 Само-улучшение — 04.08.2026 00:30 MSK

## Что сделано: curious_agent — поиск был мёртв 2 дня, починен

### Баг 1: DDG HTML отдаёт anomaly-страницу (200, но без result__a ссылок)
- `web_search_standalone` возвращал пустой список → curious_agent молча не учился
- Фикс: fallback-цепочка `html.duckduckgo.com` → `lite.duckduckgo.com` → `api.duckduckgo.com` (Instant Answer, без ключа)
- Парсеры: `_extract_ddg_html`, `_extract_ddg_lite`, `_extract_ddg_api`
- lite-DDG использует одинарные кавычки в `class='result-snippet'` — regex адаптирован

### Баг 2: отравленный topics_searched
- Сломанный поиск помечал темы «исследованными» даже при 0 результатов → темы навсегда пропускались
- Фикс: в `run_research` темы из topics_searched без findings удаляются (безусловно); провал поиска больше НЕ помечает тему исследованной (retry в следующем запуске)

### Баг 3 (главный): `"error" not in str(sources[0])` — подстрока в сериализованном dict
- Любой нормальный результат с 'errors' в заголовке ('syntax errors', 'error handling') считался провалом
- Для тем про ошибки это срабатывало ВСЕГДА → findings никогда не сохранялись
- Фикс: проверка по КЛЮЧУ dict (`"error" not in result["sources"][0]`), fail-результат = `{"error": ...}`

### Верификация
- SYNTAX OK
- fallback-цепочка: 5 результатов со сниппетами (lite-DDG)
- `run_research(force=True)` → `status: ok`, 1 тема, 5 источников в curious_knowledge.json

---

## Что сделано (2-й цикл): error_pattern_learner — trend-aware прогноз рисков

### Проблема
`predict_risks` помечал HIGH ВСЕ паттерны со streak >= 3 — даже те, что уже
пропали из свежих сканов (streak лишь декрементится). Отсюда 8 одинаковых
задач «Investigate and fix pattern» в self_directed_queue с streak 5 — шум.

### Фикс
- `_pattern_trend(data, pattern, window=6)` — тренд по ПРИСУТСТВИЮ паттерна
  в сканах (count > 0): первая vs вторая половина окна → rising/stable/falling/new
- `predict_risks`: HIGH только для rising/stable; falling → low (не засоряет очередь)
- `get_report`: в строке риска теперь `HIGH (stable): ... (тренд: stable)`

### Верификация
- SYNTAX OK
- Юнит-тест 11/11 PASS: stable→high, falling→low, rising→high, new, short-history
  no-risks, empty-data no-risks, advisory-риск без 'pattern' не ломает словарь
- Live `report`: тренды отображаются (все текущие — stable, корректно)

---
*Сгенерировано AGI cron 04.08.2026 (2-й цикл)*
