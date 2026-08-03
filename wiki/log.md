# Wiki: лог

Хронологический, append-only. Формат записи: `## [YYYY-MM-DD] тип | заголовок`,
тип — `ingest | query | lint`. `grep "^## \[" wiki/log.md | tail -5` — последние 5 записей.

## [2026-08-03] ingest | Bootstrap вики из CLAUDE.md и README.md

Первичный ingest: развернул структуру вики (`wiki/index.md`, `wiki/log.md`,
`wiki/pages/`, `wiki/raw/`) и заполнил пять страниц матчасти/roadmap из
существующих `CLAUDE.md` и `README.md` — протокол push, AQI/NowCast, инвариант
корзин `/history`, деплой/CI, roadmap и принятые решения. Внешние источники
(AirNow TAD и т.п.) пока не заведены — `wiki/raw/` пуст, это следующий ingest.
