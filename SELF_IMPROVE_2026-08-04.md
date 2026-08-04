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

---

## 🐙 Octopus Research — 04.08.2026 08:10 MSK

### Критично
- **n8n trial закончился**: Octopus News Parser (uLlROCQ1vUYtWpSL) больше не выполняется ("Your trial has ended", manual exec 20). Последний успех exec 15 (03.08 09:00). Рекомендация: платный план n8n ИЛИ замена — прямой RSS-парсинг Habr/VC/arXiv через cron (бесплатно). Пока парсер мёртв, новости берутся из web_search.

### Находки (подробности в /root/.hermes/data/octopus_knowledge.json)
1. **claude-mem (86.5K★)** — persistent context для агентов, заявлена поддержка Hermes. Изучить как дополнение к memory/Auto Dream.
2. **A-MAC / AtomMem** — admission control для памяти: 5 критериев (utility/confidence/novelty/recency/type). Применить к memory tool (MEMORY на 98%!).
3. **TAPR** — task-aware prompt rewriting. Протестировать на воркерах/судье Flash.
4. **ViSAGE** — self-correcting memory; **SciToolAgent-Evo** — self-evolving tool acquisition; **multi-model review** (валидация судьи-верификатора).
5. **NotebookLM**: auth_status=not_configured — нужен `nlm login`.

### TODO
- [ ] Решить судьбу новостного парсера (платный n8n или RSS cron)
- [ ] Оценить claude-mem (совместимость с Hermes, механика инъекции)
- [ ] Ввести критерии admission control в memory tool
- [ ] `nlm login` для NotebookLM (заодно upgrade 0.9.4 → 0.9.6)

---

## Что сделано (3-й цикл): self_directed_queue — trend-aware риски + кулдаун дублей

### Проблема
Cycle-2 сделал predict_risks trend-aware, НО consumer (agi_self_directed_queue)
продолжал читать наивные `streaks >= 2` → очередь снова была забита 8+ задачами
"Investigate and fix pattern ... (streak: 7)" приоритет 100, включая уже
починенные (gateway_already_running — gateway_guard сделан 02.08).

### Фикс
1. **agi_error_pattern_learner.py**: тренды и риски ПЕРСИСТЯТСЯ в
   error_patterns.json (`data["trends"]`, `data["risks"]` из predict_risks) —
   потребители читают готовый trend-aware результат, без пересчёта.
2. **agi_self_directed_queue.py**:
   - load_state(): читает `risks` из файла, берёт ТОЛЬКО `risk == "high"`
     (falling→low отфильтрован learner'ом); fallback на старый streak-логик,
     если файл старый (без ключа risks).
   - build_queue(): риск-задачи с кулдауном DEFAULT_COOLDOWN (6ч) — если задача
     уже исполнялась через run_next, не пере-добавляется (раньше каждый
     build_queue плодил те же задачи заново).
   - Текст задачи: `(trend: stable/rising)` вместо `(streak: N)`.
   - Убран дубль load_history() в секции дефолтных задач.

### Верификация
- SYNTAX OK (оба файла)
- learner update: trends persisted (8 stable + config_corrupt rising), 9 risks
- run_next() → done, gateway_already_running выполнен (exit 0), после rebuild
  очереди задача ИСЧЕЗЛА (кулдаун сработал), осталось 8 risk-задач — дублей нет
- config_corrupt (rising) — реальный тренд, кандидат на ручной фикс
