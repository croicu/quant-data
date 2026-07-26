# Postgres Client + Dimension Population

## Status: Brainstorm

## Problem statement

The initial bootstrap (`migrations/001_init_schema.sql`, `docs/SCHEMA.md`/`DATABASE.md`/`SETUP.md`)
shipped the star schema only — no Python code, no populated dimensions, no way to read or write
bars from code. This task is the deliberate follow-up: a read client (and eventually a write/ingest
path) against the schema, plus actually running the one-time `dim_date`/`dim_time` population SQL
from `docs/DATABASE.md` against the real database.

This content originates from `quant-scratch`'s `tasks/database_layer.md`, which envisioned this
Python client living in `quant-scratch` itself (`shared.postgres.PostgresDatabase`,
`defs.contracts.MarketDataProvider`) before the decision to split the warehouse into its own
`quant-data` repo. It now belongs here instead — `quant-scratch` will depend on `quant-data`
(directly, or via a settings-configured connection) rather than owning the Postgres client itself.

## Design decisions

Carried over from `database_layer.md`, adapted to this repo's actual package name
(`quant_data`, not `shared`/`defs`):

- **`quant_data.contracts.MarketDataProvider(Protocol)`** — `fetch_bars(ticker, start_date,
  end_date) -> list[OHLCV]`, read-only. No write methods in this first pass.
- **`quant_data.protocols.OHLCV`** — a dataclass carrying ticker + resolved date/time + OHLCV
  values, structured the way `day_chart`'s `DayBar` is in `quant-scratch` (pure data, no methods).
- **`quant_data.postgres.PostgresDatabase`** — the concrete `MarketDataProvider` implementation.
  Single connection per invocation, no pooling. Wraps database errors as `AppError`, matching the
  rest of this template's error-handling convention.
- **Settings**: add a `postgres` section to `settings.json`/`settings.local.json` (`host`, `port`,
  `user`, `password`, `dbname`) — the password belongs in `settings.local.json` (gitignored), never
  the committed `settings.json`.

## Open questions

- **Where does ingest live?** This task is read-only by design (matching `database_layer.md`'s
  original scope), but *something* has to populate `fact_market_data_1min` from a real provider
  (see `quant-scratch`'s `tasks/ibkr_tws_extended_hours.md` — IBKR is the chosen intraday source).
  Does the ingest tool live in this repo (`quant_data`, as a second CLI alongside the read client),
  or in `quant-scratch` as a new experiment package that happens to write to `quant-data`'s
  database instead of a local CSV? Undecided — revisit once IBKR's own task has moved past
  Brainstorm.
- **Connection pooling**: fine to skip for a single-CLI-invocation read pattern (matches
  `quant-scratch`'s own precedent of "single connection per CLI invocation, no pooling"). Revisit
  only if a long-running ingest process makes single-connection-per-call wasteful.
- **Driver choice**: `psycopg` (v3) vs `psycopg2` — not decided; check current recommended practice
  at implementation time rather than assuming either is still the better default.
- **Testing**: unit tests must mock `PostgresDatabase` per this repo's own coding-style rule
  (constructor/parameter injection over monkeypatching own internals) — straightforward, since
  `MarketDataProvider` is already structured as a swappable interface. Integration tests against a
  real database are optional for the MVP per `database_layer.md`'s original testing strategy; if
  added, they'd need a real (or disposable/test) Postgres instance, which raises its own
  provisioning question not yet addressed here.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
