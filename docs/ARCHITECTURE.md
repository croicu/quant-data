# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

Mirrors `quant-scratch`'s convention: one folder per app under `src/`, plus two repo-wide
non-app packages shared across all of them.

- `src/defs/` — `contracts.py` (behavioral `Protocol`s) + `protocols.py` (pure-data contracts).
  Kept separate from `shared/` deliberately: they're the specification, not owned by whichever
  package happens to implement them.
- `src/shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py` (`AppError`/
  `TaskError`), `settings.py` (`Settings`), `postgres.py` (`PostgresDatabase`), `providers/`
  (external data-source clients, e.g. `yf.py`).
- `src/ingest/` — the ingest CLI. Console script: `quant-ingest`.

There is no top-level `quant_data` package and no generic `quant-data` console script — same as
`quant-scratch` has no generic `quant-scratch` script, only per-app ones.

## Modules

### `src/defs/`

- `protocols.py` — `OHLCV`: ticker, timestamp (UTC), open/high/low/close, volume, and
  `incomplete` (defaults `False`) — set when the provider couldn't supply full data for that
  minute (see `docs/SCHEMA.md`'s `fact_market_data_1min.incomplete`).
- `contracts.py` —
  - `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]`,
    read-only. The read-side contract external consumers (`quant-scratch` and future others)
    depend on.
  - `IntraDayProvider(Protocol)`: `fetch_bars(ticker, target_date) -> list[OHLCV]` for a single
    session day. The ingest-side contract for external data sources — `ingest` depends on this
    abstraction, not concretely on whichever provider is plugged in.

### `src/shared/`

- `diagnostics.py`/`errors.py`/`settings.py` — standard `tpl-py` infra, unchanged in behavior from
  the single-package layout this was split out of.
- `settings.py` additionally defines `PostgresSettings` (`host`, `port`, `user`, `password`,
  `dbname`) and a `Settings.postgres` field, parsed from a `postgres` object under `settings.json`/
  `settings.local.json`'s `settings` key. The password belongs only in `settings.local.json`
  (gitignored). A `Settings.tickers` field (a plain array of strings, uppercased on load) is
  parsed the same way — a personal watchlist default for `quant-ingest`'s batch mode, so it also
  belongs in `settings.local.json` rather than the committed `settings.json`. `Settings.load(path,
  local_path)`'s `local_path` defaults relative to `path`'s own directory (not the process's cwd),
  so loading a test fixture's `settings.json` never accidentally picks up the real repo-root
  `settings.local.json`.
- `postgres.py` — `PostgresDatabase`: the concrete `MarketDataProvider` implementation, plus a
  `write_bars` method used only by `ingest` (not part of the `MarketDataProvider` Protocol itself —
  see "Contracts" below on why that's ergonomics, not the actual security boundary). Single
  connection per invocation (no pooling); wraps `psycopg` errors as `AppError`. Takes connection
  details purely as constructor parameters — never embeds assumptions about *how* the host is
  reached (no SSH-tunnel logic, no hardcoded endpoint), so a future move to AWS/Azure/elsewhere is
  a settings + `docs/DATABASE.md` change only, never a code change here.
- `providers/yf.py` — `YahooFinanceIntraDay`, an `IntraDayProvider` implementation wrapping
  `yfinance`. Ported from `quant-scratch`'s `shared/providers/yahoo_finance.py`, adapted to
  produce `OHLCV` (which carries its own `ticker`, unlike `quant-scratch`'s `DayBar`) and to set
  `incomplete=True` (with the value coerced to `0`/`0.0`) whenever `yfinance` returns `NaN` for
  any OHLCV field, or a literal `0` for volume — a real tick can't have zero volume, so `yfinance`
  reporting either NaN or a literal 0 both signal the same underlying problem (most commonly
  pre-market/after-hours minutes with no trades recorded). This is today's provider, not
  necessarily the long-term one — `quant-scratch`'s IBKR work (`tasks/ibkr_tws_extended_hours.md`)
  is the eventually-intended intraday source; `ingest` depends on `IntraDayProvider`, not on
  `YahooFinanceIntraDay` concretely, so swapping providers later doesn't touch `ingest/cli.py`'s
  logic.

### `src/ingest/`

`cli.py` — `quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD]] [--ticker TICKER]`:
fetches bars over an inclusive date range via an injected `IntraDayProvider` (defaults to
`YahooFinanceIntraDay`) and writes them via an injected `PostgresDatabase` factory (defaults to
constructing one from `settings.postgres`). Both dependencies are constructor-injectable per this
repo's DI-over-monkeypatching convention, so tests substitute fakes (`tests/mocks/yf.py`,
`tests/mocks/postgres.py`) instead of patching `ingest`'s own internals.

- `--ticker` is optional — omit it to fetch every ticker in `settings.tickers` instead of one.
- `--end-date` is optional — omit it (with `--start-date` given) for a single day. `--end-date`
  without `--start-date` is rejected. Omitting both falls back to
  `settings.startDate`/`settings.endDate` (same single-day-if-only-startDate-given rule there too).
- One connection is opened for the whole run and reused across every (ticker, date) pair.
- One (ticker, date) pair failing (bad ticker, no data for that day — e.g. a weekend) logs a
  warning and continues rather than aborting the rest of the range/batch; the exit code is `1` if
  anything failed, `0` if everything succeeded.
- No scheduling or IBKR integration yet; those are open items in
  `tasks/postgres_client_and_dimensions.md`.

## Data flow

A caller supplies `ticker` + a date range → `MarketDataProvider.fetch_bars` joins
`fact_market_data_1min` against `dim_ticker`/`dim_date` for that ticker/range → returns `OHLCV`
rows ordered by timestamp.

On the write side: for each (ticker, date) pair in the requested range/batch, `ingest` fetches that
ticker/day of bars from an `IntraDayProvider` → `PostgresDatabase.write_bars` upserts the ticker
into `dim_ticker` (insert-or-fetch, single round trip via `ON CONFLICT ... RETURNING`), resolves
`date_id`/`time_id` from the pre-populated `dim_date`/`dim_time` dimensions, then upserts into
`fact_market_data_1min` (idempotent re-run via `ON CONFLICT (ticker_id, date_id, time_id) DO
UPDATE`) inside one transaction — any failure within that pair (e.g. a date outside the populated
`dim_date` range) rolls back just that pair's writes, logs a warning, and the run continues with
the rest of the range/batch rather than aborting entirely. `ingest` is the sole writer; consumer
repos (`quant-scratch` and future others) only ever read. Today both read and write go through the single `quant_data`/`quant_writer` roles created
during provisioning — a dedicated read-only `quant_reader` role (no write grant, enforced at the
database-privilege level rather than just by the Python interface) is deferred until there's an
actual read consumer, per `tasks/postgres_client_and_dimensions.md`.

## Contracts

`defs/protocols.py`'s `OHLCV` and `defs/contracts.py`'s `MarketDataProvider`/`IntraDayProvider` are
the actual Python-level data contract now (see "Modules" above for their shapes). The database
schema itself (four tables: `dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) remains
the underlying persisted contract — see `docs/SCHEMA.md`.
