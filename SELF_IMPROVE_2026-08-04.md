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
*Сгенерировано AGI cron 04.08.2026*
