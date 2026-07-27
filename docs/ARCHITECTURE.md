# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

Two top-level packages under `src/`:

- `src/quant_data/` — the distribution's namespaced package.
  - `__init__.py` — plain re-exports of `MarketData`, `OHLCV`, and `create_postgres_provider`
    (`__all__` + top-level imports; no laziness needed — see "Why no laziness" below).
  - `protocols.py` — `OHLCV` (pure-data contract). **Public.**
  - `client/market_data.py` — `MarketData`, agnostic of the concrete backend (see "Contracts"
    below). **Public.**
  - `client/postgres_provider.py` — `create_postgres_provider`, today's factory for building a
    Postgres-backed provider to hand to `MarketData`. **Public.**
  - `_internal/` — **private** implementation detail, nested so its privacy is a folder-level
    fact, not just an `__init__.py` allow-list. Nothing here is exported by `quant_data`, and none
    of it should be imported directly by external consumers, even though Python doesn't stop you.
    - `contracts.py` — behavioral `Protocol`s (`MarketDataProvider`, `IntraDayProvider`).
    - `shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py`
      (`AppError`/`TaskError`/`DateOutOfRangeError`), `settings.py` (`Settings`), `postgres.py`
      (`PostgresDatabase`), `providers/` (external data-source clients, e.g. `yf.py`).
- `src/ingest/` — the ingest CLI. Console script: `quant-ingest`, no importable surface at all.

**Public surface**: external consumers (`quant-scratch`) should only depend on the `quant_data`
top level — `from quant_data import MarketData, OHLCV, create_postgres_provider`. Since
`client`/`protocols.py` hold only genuinely public content and `_internal/` holds none, the folder
split itself signals what's safe to depend on, instead of relying solely on an `__init__.py`
allow-list (see croicu/quant-data#10).

**Why nested `_internal/`, not a second top-level package**: this repo's history went through three
shapes. First, it mirrored `quant-scratch`'s flat top-level-package convention (`defs`, `shared`,
... with no common prefix, see commit 4ca99cf) — broke the moment `quant-data` was installed as a
pip dependency *alongside* `quant-scratch` in the same environment, since both repos' top-level
`defs`/`shared` packages shared the same import name and whichever installed last silently shadowed
the other's entirely (croicu/quant-data#7, reproduced both installation orders). Second, everything
moved under `quant_data.*` — collision-safe, but conflated "collision-safe" with "public":
`quant_data.client`/`quant_data.defs`/`quant_data.shared` were all equally collision-proof, but only
some of their contents were actually meant for external consumers. Third, the private half became
its own sibling top-level package, `quant_data_internal` — folder-level public/private clarity, but
this is what caused a real, reproduced circular-import crash (see "Why no laziness" below), because
a second independent top-level package with a genuine two-way dependency on the first loses a
Python import-ordering guarantee that nesting keeps. **Current (fourth) shape**: `_internal/`
nested inside `quant_data/` — same folder-level public/private clarity as the sibling-package
version, collision-safety unaffected (nesting under `quant_data.` was already sufficient — the
sibling package's own name, `quant_data_internal`, was never actually the thing preventing
collisions; `quant_data.`'s own uniqueness was), and the circular-import risk goes away because
nesting keeps Python's own import-completion ordering on your side (see below).

Cross-package imports are absolute with the full package prefix (`from
quant_data._internal.shared.diagnostics import Logger`); same-package imports stay relative (`from
.errors import ...`).

**Why no laziness is needed** (unlike the earlier sibling-package shape, which needed a
module-level `__getattr__` workaround): `quant_data._internal.shared.postgres` needs `OHLCV` from
`quant_data.protocols`, and `create_postgres_provider` needs `PostgresDatabase` from
`quant_data._internal.shared.postgres` in turn — a real two-way relationship between the public and
private halves (`MarketData` itself doesn't have this problem — see "Contracts" below — only the
Postgres-specific factory does). When `_internal` was a separate top-level package
(`quant_data_internal`), eagerly importing at `quant_data/__init__.py`'s module scope turned this
into an actual circular import whenever something touched `quant_data_internal` before `quant_data`
itself (reproduced directly: `import quant_data_internal.shared.postgres` as the first import in a
process raised `ImportError: cannot import name 'PostgresDatabase' from partially initialized
module`). Nesting removes the failure mode structurally: importing a dotted path like
`quant_data._internal.shared.postgres` requires Python to fully finish importing `quant_data` (i.e.
run its whole `__init__.py`) *before it even attempts* the `._internal` segment — so by the time
Python would try to import `quant_data._internal.shared.postgres` a second time (the self-reference
that caused the crash), it's already been loaded as a side effect of finishing `quant_data`'s own
`__init__.py`, not still mid-execution. Verified directly: re-ran the exact `import
quant_data._internal.shared.postgres`-first reproduction against the nested layout with a plain,
eager `__init__.py` — no crash. A second top-level package doesn't get this guarantee, since Python
has no "finish importing the whole first package before touching the second" rule between two
independent top-level packages.

## Modules

### `quant_data.protocols`

`OHLCV`: ticker, timestamp (UTC), open/high/low/close, volume, and `incomplete` (defaults `False`)
— set when the provider couldn't supply full data for that minute (see `docs/SCHEMA.md`'s
`fact_market_data_1min.incomplete`). Re-exported at the `quant_data` top level.

### `quant_data._internal.contracts`

- `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]` plus
  `close() -> None`, read-only. The read-side contract `MarketData` depends on (not
  `PostgresDatabase` concretely) and that `PostgresDatabase` implements (via
  `create_postgres_provider`) — not something external consumers import directly, since they use
  `MarketData` plus a factory instead.
- `IntraDayProvider(Protocol)`: `fetch_bars(ticker, target_date) -> list[OHLCV]` for a single
  session day. The ingest-side contract for external data sources — `ingest` depends on this
  abstraction, not concretely on whichever provider is plugged in.

### `quant_data._internal.shared`

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
  `settings.local.json`. `Settings.catch_up_lookback_days` (`catchUpLookbackDays`, default `7`)
  sizes `quant-ingest --catch-up`'s trailing re-fetch window.
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
- `--catch-up` is a third, mutually-exclusive way to pick the date range: instead of an explicit
  range, it re-fetches the trailing `settings.catchUpLookbackDays` days (default 7), excluding
  today (computed via an injectable `today: Callable[[], date]` parameter on `main`, defaulting to
  `date.today`, per this repo's clock-injection convention). Deliberately does no gap
  *detection* — it just re-runs the same per-day fetch+write for the whole window unconditionally.
  Because `write_bars` upserts on `(ticker_id, date_id, time_id)`, re-ingesting an already-complete
  day is a harmless no-op; a day a prior run only partially ingested (e.g. `quant-ingest` run
  manually mid-session, or interrupted) gets filled in. This is the first concrete job to come out
  of the scheduled-jobs brainstorm (`tasks/scheduled_jobs.md`, issue #3) — deliberately the
  narrowest slice of it: no jobs table, no in-DB scheduling mechanism, just a CLI flag. Actually
  running it nightly means wiring a cron/systemd timer on whatever host runs it, which stays
  outside this public repo per that brainstorm's design lean (box-specific scheduling detail
  shouldn't leak into committed source).
- One connection is opened for the whole run and reused across every (ticker, date) pair.
- One (ticker, date) pair failing (bad ticker, no data for that day — e.g. a weekend) logs a
  warning and continues rather than aborting the rest of the range/batch; the exit code is `1` if
  anything failed, `0` if everything succeeded.
- No IBKR integration yet — swapping Yahoo Finance for IBKR as the real intraday source is
  unaddressed, to become its own task if/when `quant-scratch` actually needs it.

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

`quant_data.protocols`'s `OHLCV` and `quant_data._internal.contracts`'s
`MarketDataProvider`/`IntraDayProvider` are the actual Python-level data contract now (see
"Modules" above for their shapes). The database schema itself (four tables: `dim_ticker`,
`dim_date`, `dim_time`, `fact_market_data_1min`) remains the underlying persisted contract — see
`docs/SCHEMA.md`.
