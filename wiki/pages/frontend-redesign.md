# Редизайн дашборда (коммит fe61145)

`server/static/index.html` переехал с Chart.js на собственный SVG и получил
новый визуальный язык. Мотивация и детали — коммит `fe61145`, «Redesign the
dashboard and switch history to sliding windows».

## Что поменялось

- **Графики** — свой SVG на клиенте вместо Chart.js: линия AQI с лентой
  разброса (`aqi_lo`/`aqi_hi` из `/api/v1/history`, см. [history-buckets](history-buckets.md))
  и спарклайны по метрикам. Вендоренный `server/static/chart.umd.js` удалён.
- **Цвет по метрике** — PM2.5, PM10 и AQI (спарклайн, линия и лента графика)
  красятся в цвет текущей AQI-категории; температура, влажность и давление —
  каждая в свой фиксированный цвет (терракотовый `#d97a4d`, синий `#4a90c4`,
  фиолетовый `#8b7dc4`, `METRIC_COLORS`/`metricColor()` в
  `server/static/index.html`), одинаковый в светлой и тёмной теме. При выборе
  любой метрики график получает ленту разброса её собственных
  `<метрика>_lo`/`_hi` (см. [history-buckets](history-buckets.md)), а зажим
  нижней границы оси Y нулём остаётся только для AQI — иначе отрицательная
  температура уезжала бы за кадр графика.
- **Периоды истории** — скользящие окна (`24h/7d/30d/12m/all`) вместо
  календарных `day|week|month`. Это была основная причина трогать `/history` —
  подробности инварианта в [history-buckets](history-buckets.md).
- **Шрифты** — Manrope и JetBrains Mono вендорены локально в
  `server/static/fonts/*.woff2` (`@font-face`), вместо CDN `fonts.googleapis.com`.
  **Не трогать источник при обновлении** — сверяться с реальным CSS-ответом
  `fonts.googleapis.com`, а не переименовывать/пересобирать файлы вручную (см.
  «Чего не ломать» в [CLAUDE.md](../../CLAUDE.md)).
- **Контент страницы** — AQI-герой с вердиктом на человеческом языке и
  рекомендациями, сводка «худшие часы» и за 24 часа — считаются на клиенте из
  `/api/v1/current` и `/api/v1/history`, как и раньше тренды.
- Тёмная/светлая тема — по-прежнему `prefers-color-scheme`, не поменялось.

## Что было дальше

Редизайн на этом не закончился — большой график перестал быть графиком
только AQI и стал переключаться кликом по карточкам метрик (их стало шесть,
AQI среди них). Устройство, таблица шагов осей и источник макета —
[dashboard-metric-selector](dashboard-metric-selector.md).

## Источники

- Матчасть из [CLAUDE.md](../../CLAUDE.md).
- Коммит: `fe61145` — «Redesign the dashboard and switch history to sliding windows».
- Реализация: [server/static/index.html](../../server/static/index.html), [server/static/fonts/](../../server/static/fonts/).
