# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

Three top-level packages under `src/`, split by who's allowed to depend on them:

- `src/quant_data/` — the **public** surface. Everything here is either directly re-exported or
  meant to be safe to read about.
  - `__init__.py` — re-exports `MarketData`, `OHLCV`, and `create_postgres_provider`, lazily (see
    "Why lazy" below).
  - `protocols.py` — `OHLCV` (pure-data contract).
  - `client/market_data.py` — `MarketData`, agnostic of the concrete backend (see "Contracts"
    below).
  - `client/postgres_provider.py` — `create_postgres_provider`, today's factory for building a
    Postgres-backed provider to hand to `MarketData`.
- `src/quant_data_internal/` — **private** implementation detail. Nothing here is exported by
  `quant_data`, and none of it should be imported directly by external consumers, even though
  Python doesn't stop you.
  - `contracts.py` — behavioral `Protocol`s (`MarketDataProvider`, `IntraDayProvider`).
  - `shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py`
    (`AppError`/`TaskError`/`DateOutOfRangeError`), `settings.py` (`Settings`), `postgres.py`
    (`PostgresDatabase`), `providers/` (external data-source clients, e.g. `yf.py`).
- `src/ingest/` — the ingest CLI. Console script: `quant-ingest`, no importable surface at all.

**Public surface**: external consumers (`quant-scratch`) should only depend on the `quant_data`
top level — `from quant_data import MarketData, OHLCV, create_postgres_provider`. Since
`client`/`protocols.py` hold only genuinely public content, the folder split itself signals what's
safe to depend on, instead of relying solely on an `__init__.py` allow-list (see
croicu/quant-data#10).

**Why three top-level packages, not one nested tree**: this repo initially mirrored
`quant-scratch`'s flat top-level-package convention (`defs`, `shared`, ... with no common prefix,
see commit 4ca99cf), but that broke the moment `quant-data` was installed as a pip dependency
*alongside* `quant-scratch` in the same environment — both repos' top-level `defs`/`shared`
packages shared the same import name, so whichever installed last silently shadowed the other's
entirely (see the bug report, croicu/quant-data#7, reproduced both installation orders). The fix
nested everything under `quant_data.*` for a while, but that conflated "collision-safe" with
"public" — `quant_data.client`/`quant_data.defs`/`quant_data.shared` were all equally
collision-proof, but only *some* of their contents were actually meant for external consumers.
The current split separates the two concerns:
  - **Collision safety** comes from the package *name* being specific enough that no other
    distribution would independently choose it — true of `quant_data`, and just as true of
    `quant_data_internal` (nobody else picks that literal name either). `shared` specifically
    could never be bare/flat again — `quant-scratch` has its own top-level `shared`, so that one
    name has an actual, already-reproduced collision; `quant_data_internal.shared` avoids it the
    same way `quant_data.shared` did. `ingest` has no known collision and no importable surface at
    all, so it doesn't need a distribution-specific prefix either way.
  - **Public/private** is now a folder-level fact, not just an `__init__.py` allow-list: `defs`
    used to mix `protocols.py` (public `OHLCV`) with `contracts.py` (private `Protocol`s) in one
    folder — no folder-level split could represent that. Splitting them across the two top-level
    packages (`protocols.py` into `quant_data`, `contracts.py` into `quant_data_internal`) removes
    the ambiguity: `quant_data/` is public, `quant_data_internal/` isn't, no per-file exceptions.

Cross-package imports are absolute with the full package prefix (`from
quant_data_internal.shared.diagnostics import Logger`); same-package imports stay relative (`from
.errors import ...`).

**Why lazy** (`quant_data/__init__.py`'s `__getattr__` instead of a plain top-level import):
`quant_data_internal.shared.postgres` needs `OHLCV` from `quant_data.protocols`, and
`create_postgres_provider` needs `PostgresDatabase` from `quant_data_internal.shared.postgres` in
turn — a real two-way dependency between the two packages (this is why `MarketData` itself is
agnostic of `PostgresDatabase`, per "Contracts" below — it doesn't have this problem at all, only
the Postgres-specific factory does). Eagerly importing at `quant_data/__init__.py`'s module scope
turned that into an actual circular import whenever something touched `quant_data_internal` before
`quant_data` itself (reproduced directly, back when `MarketData` still imported `PostgresDatabase`
directly: `import quant_data_internal.shared.postgres` as the first import in a process raised
`ImportError: cannot import name 'PostgresDatabase' from partially initialized module`). Deferring
every re-export into a module-level `__getattr__` (PEP 562) means merely importing `quant_data`
never pulls in the rest of the chain, so the cycle can't trigger regardless of which package gets
touched first or which re-exported name is added later.

## Modules

### `quant_data.protocols`

`OHLCV`: ticker, timestamp (UTC), open/high/low/close, volume, and `incomplete` (defaults `False`)
— set when the provider couldn't supply full data for that minute (see `docs/SCHEMA.md`'s
`fact_market_data_1min.incomplete`). Re-exported at the `quant_data` top level.

### `quant_data_internal.contracts`

- `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]` plus
  `close() -> None`, read-only. The read-side contract `MarketData` depends on (not
  `PostgresDatabase` concretely) and that `PostgresDatabase` implements (via
  `create_postgres_provider`) — not something external consumers import directly, since they use
  `MarketData` plus a factory instead.
- `IntraDayProvider(Protocol)`: `fetch_bars(ticker, target_date) -> list[OHLCV]` for a single
  session day. The ingest-side contract for external data sources — `ingest` depends on this
  abstraction, not concretely on whichever provider is plugged in.

### `quant_data_internal.shared`

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

### `ingest`

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

- `market_data.py` — `MarketData`: a thin, read-only wrapper around a `MarketDataProvider`.
  Agnostic of the concrete backend by construction — it only knows the protocol, not
  `PostgresDatabase` or any other concrete implementation, so swapping backends (a cloud store,
  something else entirely) never requires changing `MarketData` itself, only writing a new
  factory. Deliberately doesn't expose `write_bars` at all — for the Postgres backend specifically,
  the real enforcement of "no client can write" is the `quant_reader` role's DB-level privileges,
  not this class's shape, but a narrower surface is still better ergonomics regardless of backend.
  Re-exported at `quant_data` top level (`from quant_data import MarketData`).
- `postgres_provider.py` — `create_postgres_provider(host, port, dbname, user="quant_reader",
  password="")`: today's factory, builds a `PostgresDatabase` (connecting as `quant_reader` by
  default) and returns it as a `MarketDataProvider`. Also re-exported at `quant_data` top level.
  A future backend (e.g. a cloud store) gets its own sibling factory here rather than a change to
  `MarketData` or to this one function's signature.

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

`quant_data.protocols`'s `OHLCV` and `quant_data_internal.contracts`'s
`MarketDataProvider`/`IntraDayProvider` are the actual Python-level data contract now (see
"Modules" above for their shapes). The database schema itself (four tables: `dim_ticker`,
`dim_date`, `dim_time`, `fact_market_data_1min`) remains the underlying persisted contract — see
`docs/SCHEMA.md`.
