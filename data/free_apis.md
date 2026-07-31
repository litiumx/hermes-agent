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

## ПОГОДА (для осьминога/личных запросов)
| API | Проверено | Детали |
|-----|-----------|--------|
| Open-Meteo | ✅ 17.3°C МСК | api.open-meteo.com — без ключа, прогноз+история |
| 7Timer! | ❌ | 7timer.info — астро-погода |
| AviationWeather | ✅ | aviationweather.gov — NOAA |
| MET.no | ✅ | api.met.no — нужен User-Agent |

## СОЛНЕЧНАЯ АКТИВНОСТЬ (NOAA SWPC — без ключа!) ✅ проверено
| Данные | Endpoint | Пример |
|--------|----------|--------|
| Солнечные пятна | services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json | SSN: 94.4, F10.7: 138.21 (06.2026) |
| Kp индекс | .../planetary_k_index_1m.json | Kp: 0 сейчас |
| Аврора | .../ovation_aurora_latest.json | окно данных |
| Прогноз бурь | .../geomag/forecast.json | |
| Радиация (протоны) | .../goes/primary/differential-electrons-1-day.json | |

## СМОГ / КАЧЕСТВО ВОЗДУХА ✅ проверено
| API | Москва сейчас | Детали |
|-----|--------------|--------|
| **Open-Meteo Air Quality** | PM2.5: 31.3, PM10: 39.3 | air-quality-api.open-meteo.com — без ключа! |
| Purple Air | сенсор работает | purpleair.com — реальные сенсоры |
| OpenAQ | нужен ключ | openaq.org |
| AQICN | нужен ключ | aqicn.org |

## УФ-ИНДЕКС ✅ проверено
- Open-Meteo: `daily=uv_index_max` — Москва: 5.65 сегодня

## ДРУГОЕ (Environment, public-apis)
- **OpenAQ** — глобальное качество воздуха (ключ free)
- **PM2.5 Open Data Portal** — сенсоры PM2.5 (без ключа)
- **IQAir** — воздух+погода (ключ)
- **Spaceflight News** — новости космоса (без ключа)
- **NASA API** — изображения/данные (ключ free)
- **CO2 Offset** — углеродный след (без ключа)

## ПРИМЕНЕНИЕ В ПРОЕКТАХ
1. **Осьминог** → Open-Meteo погода + NOAA солнечная активность
2. **Личные запросы** → погода/смо-г/УФ для Москвы и других городов
3. **FinForge** → нет (если только клиенты не про погоду)
4. **Trading** → NOAA Kp (влияние на спутники/энергетику)
