# Wiki: tu4ka — каталог

Живая вики проекта по паттерну [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
не пересказ кода, а накопленное знание о системе — то, что не выводится
тривиально из чтения исходников. Схема (правила ingest/query/lint) — в
разделе «Wiki» [../CLAUDE.md](../CLAUDE.md).

## Требования и приёмка

- [requirements](pages/requirements.md) — свод действующих требований со стабильными ID (P/C/H/Z/D/A/U/O), чек-лист ручной приёмки для субагента `tester`

## Матчасть

- [server-package-layout](pages/server-package-layout.md) — раскладка пакета `server/`: кто каким модулем владеет, почему обращаться к глобалам через модуль, ловушка `rsync --delete` при деплое
- [protocol-airrohr-push](pages/protocol-airrohr-push.md) — формат `POST /api/v1/push`, маппинг полей, авторизация датчика
- [aqi-nowcast](pages/aqi-nowcast.md) — breakpoints EPA AQI, NowCast vs мгновенный AQI (+ lo/hi разброс), открытый TODO по источнику
- [history-buckets](pages/history-buckets.md) — скользящие окна `/history`, почему корзины привязаны к tz-сдвинутой сетке, а не к эпохе; `aqi_lo`/`_hi` и `<метрика>_lo`/`_hi` для ленты разброса
- [frontend-redesign](pages/frontend-redesign.md) — редизайн дашборда: свой SVG вместо Chart.js, вендоренные шрифты, скользящие окна
- [dashboard-metric-selector](pages/dashboard-metric-selector.md) — шесть карточек и один график: `METRIC_DEFS`, шаги осей, цвет и лента разброса по метрике, курсор и тултип, откуда берётся макет
- [deploy-cicd](pages/deploy-cicd.md) — VPS, systemd, автодеплой через CI, ключ деплоя
- [domain-dns](pages/domain-dns.md) — домен `amqi.am`, зона в Cloudflare через name.am, три A-записи и почему проксирование намеренно выключено

## Roadmap/решения

- [roadmap](pages/roadmap.md) — Android API, телеграм-бот, принятые архитектурные решения и их мотивация

## Внешние источники

`wiki/raw/` пока пуст — сюда попадут страницы по внешним материалам (AirNow
TAD, документация sensor.community и т.п.), когда соответствующие источники
будут туда положены и заингестены.

Отдельно стоит помнить про два источника правды **вне репозитория**:

- макет дашборда — проект Claude Design «Дизайн датчика качества воздуха»
  (см. [dashboard-metric-selector](pages/dashboard-metric-selector.md));
- breakpoints AQI — таблица AirNow (EPA), редакция 2024
  (см. [aqi-nowcast](pages/aqi-nowcast.md)).
