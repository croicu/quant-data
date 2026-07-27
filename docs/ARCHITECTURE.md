# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

`src/quant_data/` is the sole top-level package, with one subpackage per app plus two repo-wide
non-app subpackages shared across all of them:

- `src/quant_data/defs/` — `contracts.py` (behavioral `Protocol`s) + `protocols.py` (pure-data
  contracts). Kept separate from `shared/` deliberately: they're the specification, not owned by
  whichever package happens to implement them.
- `src/quant_data/shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py`
  (`AppError`/`TaskError`/`DateOutOfRangeError`), `settings.py` (`Settings`), `postgres.py`
  (`PostgresDatabase`), `providers/` (external data-source clients, e.g. `yf.py`).
- `src/quant_data/ingest/` — the ingest CLI. Console script: `quant-ingest`.
- `src/quant_data/client/` — the read client external consumers (`quant-scratch`) actually import:
  `market_data.py` (`MarketData`). Its own subpackage, not part of `shared/`, since it's
  consumer-facing rather than cross-app infra — mirrors `ingest/` being its own subpackage for the
  write side.

**Why nested under `quant_data/`, unlike `quant-scratch`'s flat `defs`/`shared`/`day_chart`/...**:
this repo initially mirrored `quant-scratch`'s flat top-level-package convention (see commit
4ca99cf), but that broke the moment `quant-data` was actually installed as a pip dependency
*alongside* `quant-scratch` in the same environment — both repos' top-level `defs`/`shared`
packages have the same import names, so whichever installs last silently shadows the other's
entirely (see the bug report, croicu/quant-data#7, reproduced both installation orders). The fix:
`quant-scratch`'s flat convention is fine for a repo that's never installed next to anything else,
but `quant-data` is specifically meant to be installed as a dependency of other projects, so its
packages need a distribution-specific namespace. Every subpackage now lives under `quant_data.*`
(`quant_data.defs`, `quant_data.shared`, `quant_data.ingest`, `quant_data.client`); there is no
bare top-level `defs`/`shared`/`ingest`/`client` anymore. Cross-package imports are absolute with
the full `quant_data.` prefix (`from quant_data.shared.diagnostics import Logger`); same-package
imports stay relative (`from .errors import ...`).

## Modules

### `quant_data.defs`

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

### `quant_data.shared`

- `diagnostics.py`/`errors.py`/`settings.py` — standard `tpl-py` infra, unchanged in behavior from
  earlier layouts this was carried over from.
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
  connection per invocation (no pooling); wraps `psycopg` errors as `AppError`, or
  `DateOutOfRangeError` (a specific `AppError` subclass) when a bar's date falls outside the
  populated `dim_date` range. Takes connection details purely as constructor parameters — never
  embeds assumptions about *how* the host is reached (no SSH-tunnel logic, no hardcoded endpoint),
  so a future move to AWS/Azure/elsewhere is a settings + `docs/DATABASE.md` change only, never a
  code change here.
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

### `quant_data.ingest`

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
- No scheduling or IBKR integration yet — recurring/unattended runs and swapping Yahoo Finance for
  IBKR as the real intraday source are unaddressed, to become their own tasks if/when
  `quant-scratch` actually needs them.

### `quant_data.client`

`market_data.py` — `MarketData`: a thin, read-only wrapper around
`quant_data.shared.postgres.PostgresDatabase`, connecting as `quant_reader` by default. This is
what external consumers (`quant-scratch`) actually import — it deliberately doesn't expose
`write_bars` at all, on top of (not instead of) `quant_reader`'s DB-level `SELECT`-only privileges
being the real enforcement.

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
repos (`quant-scratch` and future others) only ever read, via `MarketData`/`quant_reader` —
a `SELECT`-only, trust-authenticated role (no DB password; the SSH tunnel/key is the actual gate)
with no write grant at all, enforced at the database-privilege level, not just by the Python
interface (verified directly: a write attempt through `quant_reader` gets a real Postgres
`permission denied`, not just a missing method).

## Contracts

`quant_data.defs.protocols`'s `OHLCV` and `quant_data.defs.contracts`'s
`MarketDataProvider`/`IntraDayProvider` are the actual Python-level data contract now (see
"Modules" above for their shapes). The database schema itself (four tables: `dim_ticker`,
`dim_date`, `dim_time`, `fact_market_data_1min`) remains the underlying persisted contract — see
`docs/SCHEMA.md`.
