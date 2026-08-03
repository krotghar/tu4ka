# AQI: breakpoints и NowCast

Расчёт US EPA AQI живёт в [server/aqi.py](../../server/aqi.py) — чистые функции
без внешних зависимостей, переиспользуются веб-мордой и будущим ботом/клиентом.

## Breakpoints

PM2.5/PM10 breakpoints — редакция **2024 года**, источник: AirNow Technical
Assistance Document, Table 6. Итоговый AQI = максимум sub-index'ов PM2.5 и
PM10; в ответе API помечается «определяющий» загрязнитель (`dominant`).

**Не подгонять breakpoints «по памяти».** При любой правке — сверяться с
первоисточником (AirNow TAD), не с интуицией. Самотест на эталонных точках
живёт в коде: PM2.5 12.0→56, 35.4→100; PM10 155→101.

## NowCast

`/api/v1/current` отдаёт AQI по методу **NowCast** — 12-часовое взвешенное
скользящее среднее, штатный метод EPA для отображения AQI в реальном времени
(`method: "nowcast_12h"` в ответе).

`/api/v1/history` отдаёт **мгновенный** AQI по средним PM в каждой корзине —
не NowCast, иначе тренд на графике смазывается сглаживанием.

## Категории

`category`: `good | moderate | usg | unhealthy | very_unhealthy | hazardous`.
Официальные цвета категорий EPA и человекочитаемые подписи — на клиенте
([server/static/index.html](../../server/static/index.html)), не в API.

## Открытый вопрос для следующего ingest

Таблица breakpoints 2024 (AirNow TAD, Table 6) сейчас числится как источник
только по ссылке в коде/CLAUDE.md — сам документ не заведён как raw source в
`wiki/raw/`. Стоит положить туда PDF/копию таблицы при следующем ingest, чтобы
сверяться можно было офлайн и без риска, что EPA поменяет страницу.

## Источники

- Матчасть из [CLAUDE.md](../../CLAUDE.md).
- Реализация и самотест: [server/aqi.py](../../server/aqi.py).
