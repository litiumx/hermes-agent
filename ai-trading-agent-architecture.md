# Архитектура AI-Агента для Авто-Трейдинга

## Общая схема: 8 специализированных агентов

Мульти-агентная архитектура, где каждый агент — изолированный Claude Code (CC) процесс, запускаемый через `hermes-claude` с флагами `-p` (prompt) и `--output-format json`. Координация — через центральный оркестратор (файловый шина: JSON-файлы в `/tmp/trading/` + SQLite для состояния).

```
                    ┌─────────────┐
                    │ Orchestrator│ (hermes-claude, loop every 5m)
                    └──┬──┬──┬──┬──┘
        ┌──────────────┤  │  │  ├──────────────┐
        ▼              ▼  │  │  ▼              ▼
   ┌─────────┐   ┌────────┐│┌─────────┐   ┌──────────┐
   │ Research │   │  News  │││Calendar │   │Monitoring │
   └────┬─────┘   └───┬────┘│└────┬─────┘   └────┬─────┘
        │              │     │     │              │
        └──────┬───────┘     │     └──────┬───────┘
               ▼             │            ▼
        ┌──────────┐         │     ┌──────────┐
        │ Backtest │◄────────┘     │   Risk   │
        └─────┬────┘               └────┬─────┘
              │                         │
              └─────────┬───────────────┘
                        ▼
                 ┌──────────────┐
                 │  Execution   │──► Брокер API (Alpaca/IBKR)
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │  Portfolio   │──► P&L, позиции, ребаланс
                 └──────────────┘
```

## 1. Research Agent — анализ рынка и генерация гипотез

**Запуск:** `hermes-claude -p "Analyze SPY QQQ IWM: technical patterns (RSI, MACD, VWAP), support/resistance, volume anomalies. Output JSON with signal strength 0-100."`

**Что делает:**
- Технический анализ: скользящие, полосы Боллинджера, RSI-дивергенции
- Фундаментальный скоринг: P/E, PEG, FCF yield через yfinance API
- Межрыночный анализ: корреляции, секторные ротации
- Вывод: `{ticker, direction: LONG|SHORT|NEUTRAL, confidence, thesis, timeframe}`

**Интеграция с CC:** Вызывает Python-скрипт `research.py` через `subprocess.run()`. CC пишет промпт → Python исполняет pandas/ta/yfinance → JSON-ответ парсится CC и валидируется.

## 2. Backtest Agent — валидация стратегий

**Запуск:** `hermes-claude -p "Backtest strategy from /tmp/trading/signal_SPY.json: entry/exit rules, position sizing 2% risk, 2020-2026. Output: Sharpe, maxDD, win_rate, profit_factor."`

**Что делает:**
- Забирает сигнал Research Agent'а как вход
- Backtrader/Zipline: прогон на исторических данных (Yahoo Finance CSV)
- Метрики: Sharpe ratio, максимальная просадка, win rate, profit factor, Calmar
- Walk-forward анализ: out-of-sample тест на последних 6 мес
- Монте-Карло: 1000 симуляций shuffled returns для оценки luck factor
- Вывод: `{approved: bool, metrics, worst_case_drawdown, confidence_interval}`

## 3. Risk Agent — контроль рисков перед исполнением

**Запуск:** `hermes-claude -p "Evaluate risk for /tmp/trading/signal_SPY.json. Current portfolio in /tmp/trading/portfolio.json. Max sector exposure 30%, max position 10%, VaR 2%."`

**Что делает:**
- Value-at-Risk (VaR) и Conditional VaR: исторический + параметрический
- Корреляционная матрица: overlap с существующими позициями
- Sector/asset class exposure check
- Kill-switch: стоп-лосс, трейлинг-стоп уровни
- Режим risk-off: при VIX > 30 — автоснижение leverage до 0.5x
- Вывод: `{approved, position_size_adj, stop_loss, take_profit, max_risk_pct}`

## 4. Execution Agent — отправка ордеров брокеру

**Запуск:** `hermes-claude -p "Execute order from /tmp/trading/approved_orders.json via Alpaca API. Paper=true for first 100 trades."`

**Что делает:**
- Читает финальный утверждённый ордер (прошедший Research → Backtest → Risk)
- Alpaca Trade API v2: bracket orders (entry + stop-loss + take-profit)
- Smart order routing: TWAP/VWAP для крупных позиций (>5% ADTV)
- Retry logic: exponential backoff при 429/503
- Запись в execution log: fill price, slippage, latency
- Вывод: `{order_id, filled_qty, avg_price, slippage_bps}`

**Интеграция с CC:** CC генерирует Python-вызов Alpaca SDK → проверяет HTTP-статус → парсит fill.

## 5. Monitoring Agent — real-time отслеживание

**Запуск:** `hermes-claude -p "Monitor open positions from /tmp/trading/positions.json. Alert if drawdown >3% or gap risk detected."`

**Что делает:**
- WebSocket стрим от брокера (Alpaca `streamQuote`, `streamTrade`)
- Алерты: просадка >3%, неисполненный стоп-лосс, gap risk
- Telegram/webhook уведомления через `hermes send`
- Сохранение OHLCV тиков в SQLite для пост-анализа
- Heartbeat: если 60с без данных — эскалация

## 6. News Agent — фундаментальный катализатор

**Запуск:** `hermes-claude -p "Scan news for SPY AAPL MSFT past 24h: earnings, Fed, macro. Score sentiment -10 to +10."`

**Что делает:**
- RSS/API: Bloomberg, Reuters, SEC Edgar filings
- FinBERT NLP: sentiment scoring заголовков
- Классификация: earnings surprise, M&A, regulatory, macro
- Вывод: `{ticker, sentiment_score, material_events[], urgency: high|med|low}`

## 7. Calendar Agent — экономические события

**Запуск:** `hermes-claude -p "Get economic calendar for next 7 days: FOMC, CPI, NFP, PMI. Mark high-impact events."`

**Что делает:**
- Trading Economics API / investing.com парсинг
- Предупреждение за 24ч/1ч до high-impact событий
- Автоматический risk-off за 30 мин до FOMC/NFPR: закрыть спекулятивные позиции
- Вывод: `{events[], nearest_high_impact, auto_hedge_required}`

## 8. Portfolio Agent — управление капиталом

**Запуск:** `hermes-claude -p "Rebalance portfolio: target 60/40, max drawdown 15%. Current: /tmp/trading/portfolio.json"`

**Что делает:**
- Текущая аллокация vs целевая
- Tax-loss harvesting: автоматическая фиксация убытков
- Rebalance triggers: отклонение >5% от target
- P&L daily calculation
- Вывод: `{rebalance_orders[], tax_loss_candidates[], daily_pnl}`

## Как Claude Code автоматизирует цепочку

### Конвейер сигналов (каждые 15 мин cron):

```bash
# 1. Research → гипотезы
hermes-claude -p "$(cat prompts/research.txt)" \
  --output-format json > /tmp/trading/research.json

# 2. Backtest → валидация
cat prompts/backtest.txt | sed "s/SIGNAL_FILE/$(cat /tmp/trading/last_signal)/" | \
  hermes-claude --output-format json > /tmp/trading/backtest.json

# 3. Risk → проверка
jq -r '.approved' /tmp/trading/backtest.json | grep true && \
  hermes-claude -p "file://prompts/risk.txt" > /tmp/trading/risk.json

# 4. Execution → брокер
jq -r '.approved' /tmp/trading/risk.json | grep true && \
  hermes-claude -p "file://prompts/execute.txt" > /tmp/trading/order_result.json
```

### Ключевые преимущества CC в трейдинге:

| Аспект | Реализация |
|--------|-----------|
| **Детерминизм** | Все расчёты через `subprocess.run("python backtest.py")` внутри CC. LLM — только оркестратор, не калькулятор |
| **Sandbox** | Каждый CC в docker container — изоляция стратегий |
| **Логирование** | STDERR каждого CC → `/var/log/trading/` |
| **Paper trading** | Флаг `PAPER=true` в env — Alpaca paper endpoint |
| **Kill switch** | `kill -9 $(pgrep hermes-claude)` при risk-off |

### Файловая шина (inter-agent communication):

```
/tmp/trading/
├── signals/         # Research → Backtest
│   └── SPY_2026-07-23.json
├── backtests/       # Backtest → Risk
├── approved/        # Risk → Execution
├── orders/          # Execution → Broker log
├── portfolio.json   # Execution → Portfolio
├── news_feed.json   # News → Research
├── calendar.json    # Calendar → Risk
└── state.db         # SQLite: positions, P&L history, agent heartbeats
```

### Cron-расписание (внутри `hermes cron`):

```
every 5m   → Monitoring Agent (health check)
every 15m  → Research + News pipeline
hourly     → Backtest (по новым сигналам)
on new signal → Risk → Execution (event-driven)
daily 16:00 EST → Portfolio rebalance
daily 08:00 EST → Calendar Agent
```

### Безопасность:

- Все API-ключи в `.env` (Alpaca, Polygon, Telegram)
- Paper trading минимум 30 дней перед live
- Max daily loss 2% → автостоп
- Execution Agent: double-confirm на ордера >$10K
- Hermes gateway → Telegram: алерты critical priority
