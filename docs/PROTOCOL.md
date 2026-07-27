# PROTOCOL.md

CLI signature and file format schemas for `quant-data`.

## CLI

<!-- Command name, arguments, flags, exit codes. -->

### `quant-ingest`

- Usage: `quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD]] [--ticker TICKER] [--debug]`
- Fetches 1-minute OHLCV bars from Yahoo Finance and writes them into the warehouse — see
  `docs/ARCHITECTURE.md` for the full design.
- `--ticker` — single ticker (e.g. `AAPL`); omit to use every ticker in `settings.tickers` instead.
- `--start-date` — first trading date, `YYYY-MM-DD`; omit to use `settings.startDate`.
- `--end-date` — last trading date (inclusive); omit (with `--start-date` given) for a single day.
  Requires `--start-date` — rejected on its own.
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error, for upfront failures (settings load, no ticker/date
  configured at all).
- Exit codes: `0` every (ticker, date) pair succeeded; `1` settings load failure, no
  ticker/date-range configured at all, or one or more (ticker, date) pairs failed (a failing pair
  logs a warning and the run continues rather than aborting — `1` here can mean "partial success",
  not necessarily "nothing happened"); `2` argument parsing error (argparse's default behavior on
  missing/bad args, e.g. malformed dates or `--end-date` without `--start-date`).

There is no generic `quant-data` command — `quant-ingest` (write side, package `ingest`, outside
the `quant_data` namespace — no importable surface, console script only) and `quant_data.MarketData`
(read side — a library, not a CLI) are the two consumer-facing entry points. `MarketData`,
`OHLCV`, and `create_postgres_provider` are re-exported at the `quant_data` top level
(`from quant_data import MarketData, OHLCV, create_postgres_provider`); `quant_data._internal.*` is
private (nested, not a separate package) and should not be imported directly by external consumers.

## File formats

<!-- Schemas for any files this project reads or writes. -->

This repo's primary "file format" is the database schema itself — see `docs/SCHEMA.md` for the
four-table star schema (`dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) and
`migrations/001_init_schema.sql` for the exact DDL.

### Migration files (`migrations/*.sql`)

Plain numbered SQL files (`NNN_description.sql`), applied manually via `psql` in order — see
`docs/DATABASE.md`. Each migration wraps its DDL in a single transaction and records itself in the
`schema_migrations` table on success.
