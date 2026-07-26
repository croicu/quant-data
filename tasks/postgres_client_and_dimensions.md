# Postgres Client + Ingest

## Status: Brainstorm

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

- **Ingest tool shape**: decided that ingest lives in this repo (`quant-data`, as `src/ingest/` —
  see the reorg in `docs/ARCHITECTURE.md`), as a second CLI alongside the read client — not in
  `quant-scratch`. Still open: exact CLI shape, and how it
  pulls from IBKR (see `quant-scratch`'s `tasks/ibkr_tws_extended_hours.md` — IBKR is the chosen
  intraday source); revisit the IBKR integration specifics once that task has moved past
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
