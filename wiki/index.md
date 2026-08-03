# Wiki: tu4ka — каталог

Живая вики проекта по паттерну [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
не пересказ кода, а накопленное знание о системе — то, что не выводится
тривиально из чтения `server/main.py`. Схема (правила ingest/query/lint) — в
разделе «Wiki» [../CLAUDE.md](../CLAUDE.md).

## Матчасть

- [protocol-airrohr-push](pages/protocol-airrohr-push.md) — формат `POST /api/v1/push`, маппинг полей, авторизация датчика
- [aqi-nowcast](pages/aqi-nowcast.md) — breakpoints EPA AQI, NowCast vs мгновенный AQI, открытый TODO по источнику
- [history-buckets](pages/history-buckets.md) — почему корзины `/history` привязаны к `period_start`, а не к эпохе
- [deploy-cicd](pages/deploy-cicd.md) — VPS, systemd, автодеплой через CI, ключ деплоя

## Roadmap/решения

- [roadmap](pages/roadmap.md) — Android API, телеграм-бот, принятые архитектурные решения и их мотивация

## Внешние источники

Пока пусто — сюда попадут страницы по внешним материалам (AirNow TAD,
документация sensor.community и т.п.) после того, как соответствующие raw
источники появятся в `wiki/raw/` и будут заингестены.
