# Редизайн дашборда (коммит fe61145)

`server/static/index.html` переехал с Chart.js на собственный SVG и получил
новый визуальный язык. Мотивация и детали — коммит `fe61145`, «Redesign the
dashboard and switch history to sliding windows».

## Что поменялось

- **Графики** — свой SVG на клиенте вместо Chart.js: линия AQI с лентой
  разброса (`aqi_lo`/`aqi_hi` из `/api/v1/history`, см. [history-buckets](history-buckets.md))
  и спарклайны по метрикам. Вендоренный `server/static/chart.umd.js` удалён.
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
