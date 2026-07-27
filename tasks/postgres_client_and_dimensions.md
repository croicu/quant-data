# Postgres Client + Ingest

## Status: Brainstorm (first cut + batch/settings increment shipped — see closed [issue #4](https://github.com/croicu/quant-data/issues/4) and [issue #5](https://github.com/croicu/quant-data/issues/5); remaining scope below)

## Problem statement

The initial bootstrap (`migrations/001_init_schema.sql`, `docs/SCHEMA.md`/`DATABASE.md`/`SETUP.md`)
shipped the star schema only — no Python code, no way to read or write bars from code. This task is
the deliberate follow-up: both a read client and the ingest/write path against the schema, both
living in this repo.

PostgreSQL is now provisioned on `CroicuWS1` and `dim_date`/`dim_time` are populated (see the
now-closed ad-hoc infra issue).

**Single-writer, many-reader**: `quant-data` is the sole author of the data — it owns ingest and is
the only thing with write access to the database. `quant-scratch` and any future consumer repos are
read-only clients, going through `MarketDataProvider`; no client should hold write credentials to
the database at all. This resolves the "where does ingest live" question below in favor of this
repo, and turns "no client should write" from a convention into a requirement enforced at the
database-privilege level (see the new open question on role separation).

This content originates from `quant-scratch`'s `tasks/database_layer.md`, which envisioned this
Python client living in `quant-scratch` itself (`shared.postgres.PostgresDatabase`,
`defs.contracts.MarketDataProvider`) before the decision to split the warehouse into its own
`quant-data` repo. It now belongs here instead — `quant-scratch` will depend on `quant-data`
(directly, or via a settings-configured connection) rather than owning the Postgres client itself.

## Design decisions

Carried over from `database_layer.md`, adapted to this repo's actual package layout
(`src/defs/`, `src/shared/`, `src/ingest/` — see `docs/ARCHITECTURE.md`):

- **`defs.contracts.MarketDataProvider(Protocol)`** — `fetch_bars(ticker, start_date,
  end_date) -> list[OHLCV]`, read-only. No write methods in this first pass.
- **`defs.protocols.OHLCV`** — a dataclass carrying ticker + resolved date/time + OHLCV
  values, structured the way `day_chart`'s `DayBar` is in `quant-scratch` (pure data, no methods).
- **`shared.postgres.PostgresDatabase`** — the concrete `MarketDataProvider` implementation.
  Single connection per invocation, no pooling. Wraps database errors as `AppError`, matching the
  rest of this template's error-handling convention. Lives in `shared/` (not `ingest/`) since
  external consumers import it directly as the read client, same as `ingest` would for its own
  reads — it isn't owned by one app.
- **Settings**: add a `postgres` section to `settings.json`/`settings.local.json` (`host`, `port`,
  `user`, `password`, `dbname`) — the password belongs in `settings.local.json` (gitignored), never
  the committed `settings.json`.
- **Transport/endpoint-agnostic by construction**: the current box (`CroicuWS1`, reached over an
  SSH tunnel — see `docs/DATABASE.md`) is today's hosting choice, not an architectural given. It
  may move to AWS RDS, Azure Database for PostgreSQL, or elsewhere later. `PostgresDatabase` must
  only ever take connection details from `settings.json`/`settings.local.json` (`host`, `port`,
  `user`, `password`, `dbname`, and an `sslmode`/similar field once a cloud target needs one) — it
  must never embed assumptions about *how* that host is reached (no SSH-tunnel logic, no
  hardcoded endpoint). A future migration to a different host/provider should be a settings change
  plus a `docs/DATABASE.md` update, never a code change in `PostgresDatabase`, `MarketDataProvider`,
  or any client. Setting up reachability itself (opening a tunnel, configuring a VPC/security
  group, whatever a given host requires) is the operator's job, done before the app runs — not
  something the client's code manages.
- **Distribution: git dependency, not PyPI**: consumers install the `quant-data` distribution
  directly from this repo — `pip install "git+https://github.com/croicu/quant-data.git@<ref>"`,
  pinned to a tag or commit SHA, not a published PyPI package. This is an internal data-layer
  package for a known, small set of consumer repos (`quant-scratch` today, others later per
  Cross-Repo Coordination), not a general-purpose public library, so PyPI packaging/versioning
  overhead doesn't pay for itself. `defs.contracts.MarketDataProvider`/`defs.protocols.OHLCV` are
  the public contract consumers import (see `docs/ARCHITECTURE.md` for the full package layout).
- **The Python contract is ergonomics, not the security boundary**: because consumers install the
  `quant-data` distribution as a real package, they get `psycopg` and can instantiate
  `shared.postgres.PostgresDatabase` themselves with their own connection settings — a live DB
  connection, not a narrow read-only RPC surface. `MarketDataProvider` omitting a write method
  stops accidental misuse, not deliberate or buggy writes via `psycopg` directly. The actual
  enforcement of "no client can write" is the DB-role split below — connection privileges, not the
  Python interface.
- **Two DB roles: `quant_reader` and `quant_writer`** — resolves the role-separation open question.
  `quant_writer` is password-protected (`scram-sha-256`), owns the schema, read/write; used only by
  this repo's own ingest pipeline. `quant_reader` is `SELECT`-only and **trust-authenticated** — no
  DB password at all — for connections from `127.0.0.1`. The gate for readers is reaching that
  loopback port in the first place (i.e. holding an SSH key authorized on the box, per
  `docs/DATABASE.md`'s tunnel), not a second password on top of it. This needs role-specific
  `pg_hba.conf` lines ordered before the current generic `host all all 127.0.0.1/32 scram-sha-256`
  rule (first match wins), e.g. `host quant_data quant_reader 127.0.0.1/32 trust` placed above it.
  The existing `quant_data` role (schema owner, created during provisioning) still needs to be
  reconciled into this split — likely repurposed as (or replaced by) `quant_writer` — at
  implementation time.

## Open questions

- **`quant_reader` role**: still not created — only `quant_writer` (read/write, used by ingest)
  exists as a non-owner role so far, per the earlier decision to defer `quant_reader` until there's
  an actual read consumer. When `quant-scratch` (or another consumer) actually starts reading,
  create it then (trust-authenticated for `127.0.0.1`, per the Design decisions above).
- **IBKR as the real intraday source**: `YahooFinanceIntraDay` (`shared/providers/yf.py`)
  is today's provider, used to validate the end-to-end pipeline with real data — not necessarily
  the long-term one. `quant-scratch`'s IBKR work (`tasks/ibkr_tws_extended_hours.md`) is the
  eventually-intended intraday source. Swapping providers means writing a new `IntraDayProvider`
  implementation; `ingest/cli.py` itself shouldn't need to change, since it already depends on the
  `IntraDayProvider` Protocol, not concretely on Yahoo Finance.
- **Recurring/unattended scheduling**: `quant-ingest` now handles multi-ticker (`settings.tickers`,
  used when `--ticker` is omitted) and multi-day (`--start-date`/`--end-date`) batches in one
  invocation, tolerating individual (ticker, date) failures without aborting the rest. Still run
  manually, though — nothing triggers it on a schedule yet. That likely intersects with
  `tasks/scheduled_jobs.md` (currently postponed) once ingest needs to run unattended rather than
  by hand.

Resolved during implementation (moved here from earlier open questions, for history): driver is
`psycopg` (v3, with the `[binary]` extra to avoid needing a local `pg_config`/build toolchain);
connection pooling stays skipped (single connection per CLI invocation, matching the original
lean); unit tests mock `PostgresDatabase` and the Yahoo Finance provider (`tests/mocks/`) rather
than hitting a real database or network. `tests/integration/test_yf.py` (mirroring
`quant-scratch`'s own `tests/integration/test_yahoo_finance_intraday.py`) does hit the real
`yfinance` network API for a known ticker — no `PostgresDatabase` integration test against a real
database yet, matching `database_layer.md`'s original "optional for MVP" scope for that specific
piece. Per this repo's own convention (`pytest.ini`'s `testpaths = tests`, no marker gating), the
default `pytest` run now makes one live network call.

## Implementation plan

Implemented (see `docs/ARCHITECTURE.md` for the full module layout):

- `defs.protocols.OHLCV`, `defs.contracts.MarketDataProvider`/`IntraDayProvider`
- `shared.settings.PostgresSettings` + `Settings.postgres`
- `shared.postgres.PostgresDatabase` (`fetch_bars` read path, `write_bars` upsert write path)
- `shared.providers.yf.YahooFinanceIntraDay`
- `ingest.cli` wired to fetch + write a single ticker/day, both dependencies constructor-injected
- `quant_writer` DB role created and granted read/write (see the closed provisioning issue)
- `migrations/002_add_incomplete_flag.sql` — `fact_market_data_1min.incomplete` for bars where the
  provider couldn't supply full data (e.g. missing pre-market volume)

Second increment, on top of the above (see closed issue for this batch of work):

- `Settings.tickers` (a personal watchlist default, `settings.local.json`-only) and
  `Settings.start_date`/`Settings.end_date` (`settings.startDate`/`settings.endDate`) — both
  optional CLI overrides (`--ticker`, `--start-date`/`--end-date`) fall back to these when omitted.
- `quant-ingest` batches over every (ticker, date) pair in one invocation using a single shared
  connection; one pair failing logs a warning and continues rather than aborting the run.
- `--end-date` (and `settings.endDate`) made optional — a lone `--start-date`/`settings.startDate`
  means a single day.
- Fixed a real bug in `Settings.load()`: `local_path` used to default relative to the process's
  cwd regardless of the given `path`, so any test loading a custom fixture path was silently
  merging in the real repo-root `settings.local.json` (harmless while it only held Postgres creds
  a mocked factory ignored, but became an actual test-correctness bug once `tickers` started
  driving branching logic). Now `local_path` defaults relative to `path`'s own directory.
- Ingested 6 real tickers (`SPY`, `SH`, `QQQ`, `PSQ`, `DIA`, `DOG`) for `2026-07-24` via the new
  batch mode, confirming the whole settings-driven, multi-ticker/multi-day path end-to-end.

## Test results

- `ruff format`/`ruff check` clean.
- `pytest`: 11 passed — `tests/unit/test_ingest_cli.py`, `test_postgres.py`, `test_yf.py`,
  all using mocks (`tests/mocks/postgres.py`, `tests/mocks/yf.py`), no real network/DB in the
  default run.
- Manually verified against the real database: `quant-ingest --ticker AAPL --start-date 2026-07-24 --end-date 2026-07-24`
  (CLI syntax at the time was `--date`, since replaced by `--start-date`/`--end-date`)
  fetched and wrote 953 bars; read back via `PostgresDatabase.fetch_bars` returned the same 953
  rows. `incomplete` heuristic originally only checked for `NaN`; real AAPL/2026-07-24 data had no
  NaNs but 562 literal-zero-volume bars (extended-hours minutes with no trades), which exposed that
  a real tick can't have zero volume either — the heuristic was corrected to flag literal-zero
  volume too, re-verified via a new mocked test case, and the same 953 rows were re-ingested
  (idempotent upsert, no duplicates) to pick up the corrected flag: 562 of 953 now correctly show
  `incomplete=True`.
- Second increment: `pytest` grew to 28 passed (`test_settings.py` added, `test_ingest_cli.py`
  extended for batch/range/settings-default behavior). Manually verified `quant-ingest` with no
  arguments at all (fully settings-driven: 6 tickers × 1 day) against the real database, plus the
  `--start-date`-only single-day default and CLI-overrides-settings precedence.
