# 🌐 Бесплатные API — изучено 01.08.2026
# Источник: github.com/public-apis/public-apis (2108 строк, 40+ категорий)

## КРИПТО (для trading research) — все без auth
| API | Endpoint | Лимит |
|-----|----------|-------|
| CoinGecko | api.coingecko.com/api/v3 | 10-30 req/min |
| Coinpaprika | api.coinpaprika.com/v1 | 10 req/min |
| CoinCap | rest.coincap.io/v2 | 200 req/min |
| CoinStats | api.coinstats.app/public/v1 | 50 req/min |
| CryptoCompare | min-api.cryptocompare.com | 100K/day |
| Coinlore | api.coinlore.com/api | бесплатно |

## ФИНАНСЫ (для Paperclip)
| API | Зачем | Auth |
|-----|-------|------|
| Frankfurter | Курсы валют, time series | Нет |
| ExchangeRate-API | Конвертация | apiKey free |
| Yahoo Finance (MCP) | Акции | Нет |

## ПОЛЕЗНОЕ ДЛЯ HERMES
| API | Зачем |
|-----|-------|
| GitHub API | Поиск репо, issues (уже используем) |
| Hacker News API | Новости AI (осьминог) |
| arXiv API | Papers (осьминог) |
| Reddit API | r/LocalLLaMA и др. (осьминог) |

## ПРИМЕНЕНИЕ В ПРОЕКТАХ
1. **Trading bot** → CoinGecko для цен металлов/крипты
2. **FinForge** → Frankfurter для курсов в ценах
3. **Octopus** → уже использует HN+arXiv+Reddit бесплатно
