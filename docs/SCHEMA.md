# SCHEMA.md

Table definitions, rationale, and query examples for the `quant-data` warehouse.

## Overview

A star schema: three dimensions (`dim_ticker`, `dim_date`, `dim_time`) and one fact table
(`fact_market_data_1min`). Dimensions are looked up/created once per distinct value (ticker,
calendar date, minute-of-day); the fact table holds one row per ticker per minute per trading date,
referencing all three dimensions by foreign key.

```
dim_ticker ──┐
             │
dim_date ────┼──> fact_market_data_1min
             │
dim_time ────┘
```

Rationale for a star schema over one denormalized table: dimensions avoid repeating ticker/date/
time data across millions of fact rows, keep foreign keys narrow (4-byte `INT`s) for join/index
efficiency, and set up cleanly for future aggregate fact tables (5-minute, hourly bars) that reuse
the same dimension keys without touching the raw 1-minute data.

`003_add_dim_provider_and_staging` added a fourth dimension, `dim_provider`, and a
`staging_market_data_1min` table alongside (not instead of) the fact table — see "`dim_provider`"
and "`staging_market_data_1min`" below. `004_add_reconciliation_tables` added `dim_provider.role`,
a fifth dimension (`dim_field_group`), and the `fact_reconciliation`/
`fact_reconciliation_participant`/`provider_pair_disagreement` tables `quant-reconcile` reads/
writes — see those sections below and `tasks/quant-reconcile.md`. `006_add_pending_manual_resolution`
added `fact_pending_manual_resolution`, making "stuck" an explicit, queryable state instead of an
implicit one — see its own section below. `fact_market_data_1min` itself is unchanged throughout:
it remains the single golden, reconciled dataset every reader (`MarketData`) queries, regardless of
which provider(s) a bar's value ultimately came from.

## `dim_ticker`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `SERIAL PRIMARY KEY` | |
| `ticker` | `TEXT NOT NULL UNIQUE` | Always uppercase — enforced by a `CHECK` constraint |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

## `dim_date`

| Column | Type | Notes |
|---|---|---|
| `date_id` | `SERIAL PRIMARY KEY` | |
| `date` | `DATE NOT NULL UNIQUE` | |
| `day_of_week` | `INT NOT NULL` | 0 (Monday) – 6 (Sunday), ISO-style |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

## `dim_time`

| Column | Type | Notes |
|---|---|---|
| `time_id` | `SERIAL PRIMARY KEY` | |
| `hour` | `INT NOT NULL` | 0–23 |
| `minute` | `INT NOT NULL` | 0–59 |
| `time_of_day` | `INT NOT NULL` | `HHMM` as an integer, e.g. `930` for 9:30, `1600` for 16:00 |

At most 1,440 rows total (one per minute of the day), regardless of how much history is loaded —
populated once, not per ticker/date.

## `dim_provider`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `SERIAL PRIMARY KEY` | |
| `name` | `TEXT NOT NULL UNIQUE` | Always lowercase — enforced by a `CHECK` constraint |
| `role` | `TEXT NOT NULL DEFAULT 'candidate'` | `'candidate'` or `'whistleblower'`, enforced by a `CHECK` constraint. Added in `004_add_reconciliation_tables` |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

Data-source dimension, added in `003_add_dim_provider_and_staging`. Seeded with `'yfinance'` and
`'ibkr'` — IBKR's real and paper accounts return identical market data, so both share the single
`'ibkr'` row; the account used is an execution detail, not a distinct data identity. Not hardcoded
to exactly two rows — more providers can be added later without a design change.

`role` distinguishes real candidate providers (`'ibkr'` today — data that can actually be promoted
into `fact_market_data_1min`) from a whistleblower provider (`'yfinance'` — compared against to
derive reconciliation's tolerance and completeness signals, never promoted except via a person's
manual correction; see `tasks/quant-reconcile.md`). This is the single source of truth for that
distinction — deliberately not duplicated as a separate list in `settings.json`, so it can't drift
out of sync with what's actually seeded here.

## `staging_market_data_1min`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `open`, `high`, `low`, `close` | `NUMERIC NOT NULL` | Same precision rationale as `fact_market_data_1min` |
| `volume` | `BIGINT NOT NULL` | `>= 0` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC, same as `fact_market_data_1min` |
| `incomplete` | `BOOLEAN NOT NULL DEFAULT FALSE` | Same meaning as `fact_market_data_1min.incomplete` |

Added in `003_add_dim_provider_and_staging`. Holds each provider's raw, as-ingested bars —
identical bar columns to `fact_market_data_1min`, plus `provider_id` — so multiple providers
(`yfinance`, `ibkr`) can write independently for the same ticker/date/minute without overwriting
each other. Primary key `(provider_id, ticker_id, date_id, time_id)`, matching how each
`IntraDayProvider` writes its own rows (blind to what other providers wrote for the same bar).
Written by `PostgresDatabase.write_staging_bars` — every `quant-ingest` run writes here now (issue
#22), never straight to `fact_market_data_1min`.

A staging row is purged once its bar reconciles into `fact_market_data_1min` **and** neither
adjacent minute (`t-1`/`t+1`, same ticker) is still unresolved — a resolved bar's raw rows are kept
a little longer if a neighboring bar might still need them as a Tier-3 windowed-average input, so a
bar can be promoted into `fact_market_data_1min` immediately without losing that data for a future
run (see `docs/ARCHITECTURE.md`'s `reconcile` section). A bar with staging rows still present is in
one of three states: not every configured provider has reported yet; already resolved but waiting
on a neighbor before it's safe to purge; or the providers disagree beyond tolerance and the bar has
a row in `fact_pending_manual_resolution` (see below), in which case a plain `quant-reconcile` run
skips it entirely and only `--finalize` touches it. The reconciliation logic itself — reading
staging, comparing per-field-group against a measured tolerance, promoting agreeing bars, purging
their staging rows once safe — is a separate CLI, `quant-reconcile` (same repo, same `quant-<verb>`
naming as `quant-ingest`); see `tasks/quant-reconcile.md` and `docs/ARCHITECTURE.md`'s `reconcile`
section for the full design.

## `fact_market_data_1min`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `open`, `high`, `low`, `close` | `NUMERIC NOT NULL` | Unbounded-precision, not a fixed-scale numeric or float — preserves exact precision for backtests |
| `volume` | `BIGINT NOT NULL` | `>= 0` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC, enforced by `PostgresDatabase` pinning the connection's session `TimeZone` to UTC on connect (see [issue #9](https://github.com/croicu/quant-data/issues/9)) — previously just assumed, which let an unpinned session silently shift every stored value by its own local offset; kept for reference/audit, redundant with the three dimension keys |
| `incomplete` | `BOOLEAN NOT NULL DEFAULT FALSE` | Added in `002_add_incomplete_flag`. True when the provider couldn't supply full data for this bar (e.g. missing pre-market volume) — a signal to prioritize backfilling, not a data-quality gate on read. |

Primary key: `(ticker_id, date_id, time_id)` — enforces exactly one bar per ticker per minute per
date.

## `dim_field_group`

| Column | Type | Notes |
|---|---|---|
| `field_group_id` | `SERIAL PRIMARY KEY` | |
| `name` | `TEXT NOT NULL UNIQUE` | `'ohlc'` today |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

Added in `004_add_reconciliation_tables`. Groups `fact_market_data_1min` columns that
`quant-reconcile` must resolve from a single provider together — seeded with `'ohlc'`
(`open`/`high`/`low`/`close`, which must come from one provider so a promoted bar is never
internally inconsistent, e.g. `low` > `close`). Originally also seeded a `'volume'` row, removed by
`005_remove_volume_field_group`: volume is no longer independently reconciled against the
whistleblower (see `tasks/volume_reconciliation.md`) — it now simply rides along with whichever
provider wins the `'ohlc'` group for that bar. Not hardcoded to exactly this one row — a later
migration adding a new `fact_market_data_1min` column that genuinely does need its own
cross-provider agreement assigns it to an existing or new group row, a data change, not a schema
change.

## `fact_reconciliation`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `field_group_id` | `INT NOT NULL` | FK → `dim_field_group` |
| `winning_provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `resolution_path` | `TEXT NOT NULL` | One of `'completeness'` / `'agreement'` / `'boundary_fix'` / `'finalized'` / `'manual_override'`, enforced by a `CHECK` constraint |
| `resolved_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(ticker_id, date_id, time_id, field_group_id)`. Added in
`004_add_reconciliation_tables`. One row per (bar, field group) once `quant-reconcile` resolves it
— presence of a row *is* "resolved"; a bar with no row here for one of its groups is still stuck in
`staging_market_data_1min`. `resolution_path` distinguishes `quant-reconcile`'s automatic pass
(`'completeness'` / `'agreement'` / `'boundary_fix'`) from `--finalize`'s `preferredProvider`
algorithm (`'finalized'`) from an actual person directly correcting a bar (`'manual_override'` —
the only path a whistleblower provider's value can ever reach `fact_market_data_1min` through).
Since `005_remove_volume_field_group`, rows here only ever exist for the `'ohlc'` group — a bar
promotes to fact as soon as `'ohlc'` resolves, with `volume` taken directly from the winning
provider's own staging row (see `tasks/volume_reconciliation.md`). See `tasks/quant-reconcile.md`
for the full per-tier logic.

## `fact_reconciliation_participant`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | |
| `date_id` | `INT NOT NULL` | |
| `time_id` | `INT NOT NULL` | |
| `field_group_id` | `INT NOT NULL` | |
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `won` | `BOOLEAN NOT NULL` | |

Primary key: `(ticker_id, date_id, time_id, field_group_id, provider_id)`, with a composite foreign
key on `(ticker_id, date_id, time_id, field_group_id)` back to `fact_reconciliation`. Added in
`004_add_reconciliation_tables`. One row per provider that competed for a resolved (bar, group),
win or lose — including a `role = 'whistleblower'` provider, which gets a row like any other
provider that wrote a staging row for that bar, not just the winning candidate. Doubles as the
provider-reputation record: "who tends to lose" is `WHERE won = FALSE` grouped by `provider_id`,
filtered (via a join to `fact_reconciliation`) to `resolution_path = 'manual_override'` — no
separate reputation table needed. For the whistleblower specifically, `won = TRUE` rows (always
`manual_override`, its only path to winning) are their own signal: how often a person actually
reached for its value specifically, as opposed to hand-correcting to something else entirely.

## `provider_pair_disagreement`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` — always a `role = 'candidate'` provider, never the whistleblower |
| `field_group_id` | `INT NOT NULL` | FK → `dim_field_group` |
| `sample_count` | `BIGINT NOT NULL DEFAULT 0` | `>= 0` |
| `running_mean` | `NUMERIC NOT NULL DEFAULT 0` | Signed: `candidate_value - whistleblower_value`, not the reverse |
| `running_m2` | `NUMERIC NOT NULL DEFAULT 0` | Welford's algorithm accumulator |
| `stddev` | `NUMERIC` | Denormalized from `running_m2`/`sample_count` for fast reads |
| `updated_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(provider_id, field_group_id)`. Added in `004_add_reconciliation_tables`. Running
variance of each candidate provider's disagreement against the fixed whistleblower (`yfinance`
today), per field group — measured directly rather than reconstructed from two
individually-unmeasurable per-provider "precision" figures (no ground-truth reference exists to
attribute disagreement to one side or the other). No ticker dimension — noise is a property of
methodology, not of individual tickers. `stddev` is relative/fractional, scaled against an actual
reference value at comparison time to get `quant-reconcile`'s bar-specific absolute tolerance
(`tolerance = k * stddev * reference_value`, `k = 3`). Since `005_remove_volume_field_group`, only
ever has rows for the `'ohlc'` field group — volume no longer has its own measured tolerance (see
`tasks/volume_reconciliation.md`). Only in-band (raw-agreement) observations
update this — `finalized`/`manual_override` resolutions are excluded so outliers can't gradually
widen "normal." Seeded with an illustrative starting value per field group at migration time (not
measured data), pseudo-count 100 so it fades slowly as real observations accumulate.

## `fact_pending_manual_resolution`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `field_group_id` | `INT NOT NULL` | FK → `dim_field_group` |
| `flagged_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(ticker_id, date_id, time_id, field_group_id)`. Added in
`006_add_pending_manual_resolution`. Same grain and "presence of a row is the status" convention as
`fact_reconciliation` — presence *is* "this (bar, field group) exhausted the automatic pass's Tiers
1-3 and is awaiting `--finalize` or manual correction"; absence (for a bar otherwise still in
staging) means either not yet attempted, or not yet fully evaluated this run. Makes "stuck" an
explicit, queryable state instead of an implicit one (inferred by absence of a
`fact_reconciliation` row) — a plain `quant-reconcile` run fetches/evaluates only (bar, group)s
with no row here; `--finalize` fetches only what has a row here, force-resolves it via
`settings.reconcile.preferredProvider`, and deletes the row. Prevents every future plain run from
re-fetching and re-evaluating the same known-stuck bars indefinitely, which compounds badly under a
realistic cadence (e.g. plain `quant-reconcile` daily, `--finalize` weekly) — see
`tasks/quant_reconcile.md`'s "Updated (2026-08-03)" section for the full design.

Readable externally via `MarketData.fetch_pending_resolution_bars(ticker, start_date, end_date)`
(`docs/ARCHITECTURE.md`'s `MarketDataProvider` section) — since this table itself has no `OHLCV`
columns, that method joins it against `staging_market_data_1min` (and `dim_provider`, for `role`)
to return one entry per (bar, field group, provider) still in dispute, exposing every reporting
provider's raw value *and* whether it's the candidate or whistleblower, rather than just the fact
that a bar is stuck. Requires `quant_reader` to have `SELECT` on `staging_market_data_1min`,
`dim_field_group`, and `dim_provider` in addition to this table — see `docs/DATABASE.md`'s
"Granting quant_reader access to new tables".

## Indexes

- `idx_fact_1min_ticker_date_time` on `(ticker_id, date_id, time_id)` — matches the primary key
  order; supports point lookups and range scans within a single ticker/date.
- `idx_fact_1min_ticker_date` on `(ticker_id, date_id)` — supports "give me the whole day for this
  ticker" queries without needing a specific time.
- `idx_dim_ticker_symbol`, `idx_dim_date`, `idx_dim_time_of_day` — support dimension lookups by
  their natural key (ticker symbol, calendar date, `HHMM` integer) when resolving a natural-key
  query into dimension IDs before joining the fact table.
- `idx_staging_1min_ticker_date_time` on `staging_market_data_1min(ticker_id, date_id, time_id)` —
  the opposite leading-column order from that table's own primary key. The primary key
  (`provider_id` first) matches how each provider writes its own rows; this index matches
  reconciliation's actual read pattern — gathering every provider's row for one bar.
- `idx_fact_reconciliation_resolution_path` on `fact_reconciliation(resolution_path)` — supports
  counting/filtering by how bars resolved (e.g. "how many were `finalized` vs. `manual_override`").
- `idx_fact_reconciliation_participant_provider` on
  `fact_reconciliation_participant(provider_id, won)` — supports the reputation read pattern:
  aggregating win/loss by provider without scanning the whole table.
- `fact_pending_manual_resolution` needs no index beyond its own primary key — the table only ever
  holds the pending subset (small and self-limiting, unlike `staging_market_data_1min` which grows
  with every ingested minute), so both the plain pass's per-bar existence check and `--finalize`'s
  "fetch everything pending" already hit a small, fully-indexed table either way.

## Query examples

Fetch all 1-minute bars for AAPL on 2026-01-15:

```sql
SELECT f.timestamp, f.open, f.high, f.low, f.close, f.volume
FROM fact_market_data_1min f
JOIN dim_ticker t ON t.ticker_id = f.ticker_id
JOIN dim_date d ON d.date_id = f.date_id
WHERE t.ticker = 'AAPL' AND d.date = '2026-01-15'
ORDER BY f.time_id;
```

Fetch AAPL bars during regular trading hours only (9:30–16:00) across a date range:

```sql
SELECT d.date, ti.time_of_day, f.open, f.high, f.low, f.close, f.volume
FROM fact_market_data_1min f
JOIN dim_ticker t ON t.ticker_id = f.ticker_id
JOIN dim_date d ON d.date_id = f.date_id
JOIN dim_time ti ON ti.time_id = f.time_id
WHERE t.ticker = 'AAPL'
  AND d.date BETWEEN '2026-01-01' AND '2026-01-31'
  AND ti.time_of_day BETWEEN 930 AND 1600
ORDER BY d.date, ti.time_of_day;
```

## Future extensibility

Deliberately out of scope for this initial migration (see `database_layer.md`'s original design
notes in `quant-scratch` for the full reasoning) — noted here so they aren't rediscovered from
scratch later:

- **Aggregate fact tables**: `fact_market_data_5min`, `fact_market_data_hourly`, reusing the same
  three dimension tables, populated by rolling up `fact_market_data_1min` rather than re-ingesting.
- **Trading calendar in `dim_date`**: `is_trading_day`, `market_open_time`, `market_close_time`
  columns, to distinguish holidays/weekends from real trading days without relying on ingest-time
  logic. Not needed for the schema itself yet since ingest only ever writes real trading data.
- **Table partitioning**: once `fact_market_data_1min` holds multiple years across many tickers,
  native Postgres declarative range partitioning by `date_id` (or `timestamp`) is the natural next
  step for both query performance and easier archival of old partitions. Not added now — no schema
  bloat ahead of an actual need.
- **No JSON columns**: explicit typed columns only, by design — keeps the schema queryable and
  indexable without a JSON-parsing layer.
