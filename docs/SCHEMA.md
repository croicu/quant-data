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
implicit one — see its own section below. `007_add_dim_field_and_dataset_inception` added a sixth
dimension (`dim_field`) and re-keyed `provider_pair_disagreement` to `(provider_id, ticker_id,
field_id)`; the pipeline-accuracy-hardening algorithm/CLI changes that consume the new shape (a
per-ticker graduation gate, per-field tolerance, `--backfill`) shipped as a follow-up, no further
schema change — see those sections below and
[croicu/quant-data#28](https://github.com/croicu/quant-data/issues/28)/#29.
`008_add_ingestion_coverage` added `ingestion_coverage`. `quant-reconcile` consumes it (a
candidate can promote via Tier 1 completeness when the whistleblower is confirmed absent for a
bar, not just when it reported incomplete); `quant-ingest`'s own write path (recording + coalescing
coverage on every successful fetch, `record_ingestion_coverage`) shipped 2026-08-16, closing the
gap where this table only ever reflected `008`'s one-time backfill and went stale the instant real
ingestion resumed — see its own section below and
[croicu/quant-data#31](https://github.com/croicu/quant-data/issues/31).
`009_replace_incomplete_with_data_quality` replaced `staging_market_data_1min.incomplete`/
`fact_market_data_1min.incomplete` (boolean) with a tri-state `data_quality` column (`accepted`/
`incomplete`/`rejected`) — a breaking change to `quant_data`'s public `OHLCV.incomplete` field
(now `OHLCV.data_quality: DataQuality`), laying the schema foundation for
`tasks/yahoo_data_sanitization.md`'s per-provider outlier-rejection check.
`010_add_data_quality_thresholds` added the per-(provider, ticker) coefficient table that check
uses (see its own section below); `reconcile/outlier_detection.py` and `quant-reconcile`'s new
outlier-detection pass (runs before Tiers 1-4, so a newly-rejected bar can auto-promote its
candidate in the same invocation) are what actually set `data_quality = 'rejected'` now.
Otherwise `fact_market_data_1min` remains the single golden, reconciled dataset every reader
(`MarketData`) queries, regardless of which provider(s) a bar's value ultimately came from.
`011_add_market_data_archive` widened `dim_provider.role` with a third value, `'advisor'` (seeding
`'manual'` and `'databento'`), and added `market_data_archive` — a permanent record of a bar's raw
provider value once no longer kept in `staging_market_data_1min`, closing the information-loss gap
`purge_staging_bar` had (see its own section below and
[croicu/quant-data#35](https://github.com/croicu/quant-data/issues/35)).
`012_add_timestamp_to_pending_manual_resolution` added `fact_pending_manual_resolution.timestamp`,
a schema-consistency fix bringing it in line with every other fact/staging table's denormalized
`timestamp` column (see its own section below and
[croicu/quant-data#36](https://github.com/croicu/quant-data/issues/36)).
`013_add_materiality_floor` added the per-(provider, ticker, field) minimum-tolerance table
bounding `quant-reconcile`'s Tier 2/3 tolerance below by an economically meaningful minimum;
`014_seed_materiality_floor_defaults` seeded it with volume-informed defaults for today's 6
actively-ingested tickers; `015_relax_materiality_floor_psq_dog` overrode `PSQ`/`DOG` with their
own observed-distribution P90 after live validation found `014`'s cross-ticker model under-fit
them badly (see its own section below and `tasks/materiality_floor_tolerance.md`).
`016_add_massive_provider` seeded `'massive'` (formerly Polygon.io) as a second `role = 'candidate'`
`dim_provider` row alongside `'ibkr'` — the first time reconciliation has ever had two real
candidates competing for the same bar. `017_add_unadjudicated_resolution_path` widened
`fact_reconciliation.resolution_path`'s `CHECK` with a new value, `'unadjudicated'`: tracing exactly
how two real candidates would be adjudicated surfaced a bug only reachable with more than one
candidate present (an outlier-`REJECTED` or confirmed-absent whistleblower used to be caught by
Tier 1 completeness before Tiers 2/3 ever saw it, but that assumption silently broke with a second
candidate, since completeness can no longer pick a winner between two valid candidates on its own)
— `resolve_automatic` now checks whether an `ACCEPTED` whistleblower exists at all before ever
attempting Tiers 2/3, falling through to `settings.reconcile.preferredProvider`'s raw value with
this distinct reason code (never comparing against a whistleblower value already known to be
invalid) rather than either tier's silent no-op. See
[croicu/quant-data#44](https://github.com/croicu/quant-data/issues/44) for the full design.
`quant_ingest` (croicu/quant-data#52) and `quant_schedule` (croicu/quant-data#66) are each a
**separate database**, not a `migrations/` entry against `quant_data` — see their own "`quant_ingest`
database"/"`quant_schedule` database" sections below.

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
| `role` | `TEXT NOT NULL DEFAULT 'candidate'` | `'candidate'`, `'whistleblower'`, or `'advisor'`, enforced by a `CHECK` constraint. Added in `004_add_reconciliation_tables`; widened with `'advisor'` in `011_add_market_data_archive` |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

Data-source dimension, added in `003_add_dim_provider_and_staging`. Seeded with `'yfinance'` and
`'ibkr'` — IBKR's real and paper accounts return identical market data, so both share the single
`'ibkr'` row; the account used is an execution detail, not a distinct data identity. `011` added
`'manual'` and `'databento'`, both `role = 'advisor'`. `016_add_massive_provider` added `'massive'`
(formerly Polygon.io), a second `role = 'candidate'` row alongside `'ibkr'`. Not hardcoded to
exactly these rows — more providers can be added later without a design change; `dim_provider.role`
has no cap on how many rows may hold `role = 'candidate'` at once.

`role` distinguishes real candidate providers (`'ibkr'`/`'massive'` — data that can actually be
promoted into `fact_market_data_1min`) from a whistleblower provider (`'yfinance'` — compared
against to derive reconciliation's tolerance and completeness signals, never promoted except via a
person's manual correction; see `tasks/quant-reconcile.md`) from an advisor provider (`'manual'`,
`'databento'` — can suggest a value but has no autonomous authoring rights; unlike `'candidate'`,
an advisor can never win a bar through the automatic Tier 1-3 pass, only through an explicit human
action). This is the single source of truth for that distinction — deliberately not duplicated as
a separate list in `settings.json`, so it can't drift out of sync with what's actually seeded here.

`'databento'` has zero footprint anywhere else in the schema — it's a purely out-of-band reference
a human consults before acting, never itself written to `staging_market_data_1min` or referenced by
`fact_reconciliation_participant`. `'manual'` is the existing hand-correction path
(`fact_reconciliation.resolution_path = 'manual_override'`) gaining an actual `dim_provider`
identity — see `market_data_archive` below.

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
| `data_quality` | `TEXT NOT NULL CHECK (data_quality IN ('accepted', 'incomplete', 'rejected'))` | Same meaning as `fact_market_data_1min.data_quality` |
| `wap`, `trade_count` | `NUMERIC`, `INT CHECK (trade_count IS NULL OR trade_count >= 0)` | Nullable. Added in `018_add_supplement_fields` — see `fact_market_data_1min`'s own row below for the full design; this table just holds each reporting provider's own raw value ahead of promotion. |
| `avg_bid`, `avg_ask`, `midpoint_open`, `midpoint_high`, `midpoint_low`, `midpoint_close` | `NUMERIC` | Nullable. Added in `018_add_supplement_fields`, same as above. |

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
run (see `docs/ARCHITECTURE.md`'s `reconcile` section). Since `011_add_market_data_archive`
(croicu/quant-data#35), a candidate row is archived to `market_data_archive` in the same
transaction immediately before it's deleted — "purged" no longer means "gone," see
`market_data_archive` below. The whistleblower's permanent purge exemption is unaffected: its rows
are still never archived or deleted, exactly as before. A bar with staging rows still present is in
one of three states: not every configured provider has reported yet; already resolved but waiting
on a neighbor before it's safe to purge; or the providers disagree beyond tolerance and the bar has
a row in `fact_pending_manual_resolution` (see below), in which case a plain `quant-reconcile` run
skips it entirely and only `--finalize` touches it. The reconciliation logic itself — reading
staging, comparing per-field-group against a measured tolerance, promoting agreeing bars, purging
their staging rows once safe — is a separate CLI, `quant-reconcile` (same repo, same `quant-<verb>`
naming as `quant-ingest`); see `tasks/quant-reconcile.md` and `docs/ARCHITECTURE.md`'s `reconcile`
section for the full design.

## `ingestion_coverage`

| Column | Type | Notes |
|---|---|---|
| `coverage_id` | `SERIAL PRIMARY KEY` | |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `start_date_id` | `INT NOT NULL` | FK → `dim_date` |
| `end_date_id` | `INT NOT NULL` | FK → `dim_date`. `CHECK (start_date_id <= end_date_id)` |
| `updated_at` | `TIMESTAMP` | Defaults to insert time |

Added in `008_add_ingestion_coverage` ([croicu/quant-data#31](https://github.com/croicu/quant-data/issues/31)).
One row per *contiguous* date range successfully ingested for a (ticker, provider) pair — explicit
tracking rather than deriving coverage from `staging_market_data_1min`'s presence/absence, since
staging rows get purged over time (candidates once resolved; the whistleblower never, per its
permanent purge exemption) and wouldn't stay a reliable long-term coverage signal.
`PostgresDatabase.record_ingestion_coverage` (`quant-ingest`'s write path, the piece #31 originally
shipped without) now keeps this table live: called once per `(ticker, provider, date)` right after
both the fetch and the staging write for it succeed, marking that date covered — coalescing into
any existing adjacent/overlapping range (extending one range, or merging two if the new date
exactly bridges a previously-separate pair) rather than adding one row per day. A raised
`AppError` from either the fetch or the write — including a confirmed-empty whole day (e.g.
Yahoo's `history.empty` case for a weekend) — does *not* mark coverage; distinguishing that from a
genuine fetch failure is deliberately left to the postponed `tasks/ingest_error_classification.md`,
not solved here. Recording coverage failing on its own (a separate DB round trip from the staging
write) is logged and skipped, not fatal to that `(ticker, date)` — the bars themselves are already
safely written either way.

**Motivating case, and consumed by `quant-reconcile` today**: Tier 1 (completeness) could
previously only promote a candidate's value when the whistleblower reported but was flagged
`incomplete` — if the whistleblower simply never wrote a row for a minute at all (common: Yahoo
doesn't emit a row for a real no-trade/no-volume minute), that bar was excluded from reconciliation
entirely and the candidate's legitimate data sat in staging forever. `_run_automatic_pass` now
distinguishes "whistleblower confirmed absent for this minute" (its date range was ingested;
promote the candidate, same `'completeness'` resolution_path, no new path — see
`docs/ARCHITECTURE.md`'s `reconcile` section for the exact mechanism) from "whistleblower not
ingested yet" (bar left alone, not even marked pending, exactly as if a required provider were
simply missing — must wait for a future run) — live-tested against CroicuWS1: 6,939 of 7,192 stuck
`ibkr` staging rows were exactly the former case.

The initial backfill (baked into `008_add_ingestion_coverage` itself, not a separate script)
populates this table from whatever was in `staging_market_data_1min` at migration-apply time, using
a "gaps and islands" contiguous-run query (`dim_date.date_id` increments by exactly 1 per calendar
day including weekends, so a true gap — a weekend, or a day never ingested — correctly stays a
separate range rather than being bridged by a naive `MIN`/`MAX`). No-op on a fresh bootstrap
database with empty staging.

## `fact_market_data_1min`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `open`, `high`, `low`, `close` | `NUMERIC NOT NULL` | Unbounded-precision, not a fixed-scale numeric or float — preserves exact precision for backtests |
| `volume` | `BIGINT NOT NULL` | `>= 0` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC, enforced by `PostgresDatabase` pinning the connection's session `TimeZone` to UTC on connect (see [issue #9](https://github.com/croicu/quant-data/issues/9)) — previously just assumed, which let an unpinned session silently shift every stored value by its own local offset; kept for reference/audit, redundant with the three dimension keys |
| `data_quality` | `TEXT NOT NULL CHECK (data_quality IN ('accepted', 'incomplete', 'rejected'))` | Added in `002_add_incomplete_flag` as a boolean `incomplete`; replaced by `009_replace_incomplete_with_data_quality` with this tri-state column. `accepted` is the normal case; `incomplete` means the provider couldn't supply full data for this bar (e.g. missing pre-market volume) or a plausibility check couldn't be run against it; `rejected` means a per-provider staging-quality check ran and found the value implausible (`tasks/yahoo_data_sanitization.md`). `rejected` is treated identically to `incomplete` by reconcile's Tier 1 completeness check — the distinction is for audit/debugging, not different promotion behavior. Not a data-quality gate on read — a signal to prioritize backfilling/review. |
| `wap`, `trade_count` | `NUMERIC`, `INT CHECK (trade_count IS NULL OR trade_count >= 0)` | Nullable. Added in `018_add_supplement_fields` ([croicu/quant-data#61](https://github.com/croicu/quant-data/issues/61)) — volume-weighted average price / trade count for the bar. **Trade group**: computed from the same trade prints as `open`/`high`/`low`/`close`/`volume`, so it's winner-gated exactly like `volume` already is — `quant-reconcile` copies it from whichever provider won this bar's `ohlc` vote, leaving it `NULL` if that provider didn't report it, even if a losing candidate did ("no data over bad data", an explicit tradeoff). Populated from Massive's `vw`/`n` today; `NULL` on most bars, since `ibkr` (which doesn't report these) wins the large majority of votes. |
| `avg_bid`, `avg_ask`, `midpoint_open`, `midpoint_high`, `midpoint_low`, `midpoint_close` | `NUMERIC` | Nullable. Added in `018_add_supplement_fields`, same issue. **Quote group**: a different feed (the NBBO quote book, not the trade tape) with no shared failure mode with the trade group above, so it is *not* winner-gated — `quant-reconcile` copies it from whichever staging row reports it, independent of who won `ohlc`. Populated from IBKR's `BID_ASK` (`avg_bid`/`avg_ask`) and `MIDPOINT` (`midpoint_*`, a genuine OHLC series of the bid/ask midpoint price) methods; no second quote source exists yet, so this is an uncontested "wins by default" placeholder, not a validated cross-provider comparison — correctly shaped to graduate to one later. |

Primary key: `(ticker_id, date_id, time_id)` — enforces exactly one bar per ticker per minute per
date.

Both groups are written exclusively by `quant-reconcile` (`_promote_and_lazily_purge` in
`reconcile/cli.py`), preserving the standing single-writer-to-fact invariant — `stage` only ever
writes `staging_market_data_1min`, even for the quote group, which has no gating logic of its own
that would otherwise justify `stage` writing it directly. See `docs/ARCHITECTURE.md`'s `stage` and
`reconcile` sections for how each side is populated.

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
| `resolution_path` | `TEXT NOT NULL` | One of `'completeness'` / `'agreement'` / `'boundary_fix'` / `'unadjudicated'` / `'finalized'` / `'manual_override'`, enforced by a `CHECK` constraint. `'unadjudicated'` added in `017_add_unadjudicated_resolution_path` |
| `resolved_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(ticker_id, date_id, time_id, field_group_id)`. Added in
`004_add_reconciliation_tables`. One row per (bar, field group) once `quant-reconcile` resolves it
— presence of a row *is* "resolved"; a bar with no row here for one of its groups is still stuck in
`staging_market_data_1min`. `resolution_path` distinguishes `quant-reconcile`'s automatic pass
(`'completeness'` / `'agreement'` / `'boundary_fix'` / `'unadjudicated'`) from `--finalize`'s
`preferredProvider` algorithm (`'finalized'`) from an actual person directly correcting a bar
(`'manual_override'` — the only path a whistleblower provider's value can ever reach
`fact_market_data_1min` through). `'unadjudicated'` (added alongside `'massive'` becoming a second
real candidate, croicu/quant-data#44) fires automatically, mid-automatic-pass, whenever no
`ACCEPTED` whistleblower exists to adjudicate between two or more valid candidates — resolves to
`settings.reconcile.preferredProvider`'s raw value like `'finalized'` does, but kept as its own
label since no tolerance comparison was ever attempted (unlike `'agreement'`/`'boundary_fix'`) and
no human was involved (unlike `'manual_override'`); `fact_reconciliation_participant`'s non-winning
rows for an `'unadjudicated'` resolution reflect "never compared," not "lost a comparison."
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
| `archive_id` | `INT` | FK → `market_data_archive`, nullable. Added in `011_add_market_data_archive` |

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

`archive_id` starts `NULL` on `INSERT` (`record_reconciliation` never sets it — archiving always
happens later, if at all this run, via `purge_staging_bar`) and is back-filled once that
participant's raw value is actually archived: an archived candidate row, or a `'manual'` winner
(written directly to `market_data_archive`, never staged — see below). Stays permanently `NULL`
for the whistleblower, which is never archived, and for any candidate not yet purged.

## `market_data_archive`

| Column | Type | Notes |
|---|---|---|
| `archive_id` | `SERIAL PRIMARY KEY` | |
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC, same as `staging_market_data_1min` |
| `open`, `high`, `low`, `close` | `NUMERIC NOT NULL` | Same precision rationale as `fact_market_data_1min` |
| `volume` | `BIGINT NOT NULL` | `>= 0` |
| `data_quality` | `TEXT NOT NULL CHECK (data_quality IN ('accepted', 'incomplete', 'rejected'))` | Same meaning as `staging_market_data_1min.data_quality` |
| `archived_at` | `TIMESTAMP NOT NULL` | Defaults to insert time |

Added in `011_add_market_data_archive` ([croicu/quant-data#35](https://github.com/croicu/quant-data/issues/35),
`tasks/staging_archive_before_purge.md`). Permanent, append-only record of a bar's raw provider
value once it's no longer kept in `staging_market_data_1min` — directly motivated by
`CLAUDE.md`'s "No information loss during the data processing stage" principle, which
`purge_staging_bar` was violating: once a candidate row was deleted, the original disagreement
evidence behind an already-resolved bar was gone for good (52,953 resolved bars checked
2026-08-07; only 4,101 still had both providers' original staging rows intact).

Two ways a row gets here, per the design converged in issue #35:

- **Archived candidate rows** — `purge_staging_bar` inserts here immediately before deleting a
  candidate's staging row, in the same transaction, then writes the new `archive_id` back onto
  that provider's `fact_reconciliation_participant` row. Implemented as of this migration.
  Whistleblower rows are never archived (unaffected by this table entirely) — they're already
  permanently purge-exempt in staging, per `staging_market_data_1min`'s own retention rule above.
- **Direct `'manual'` writes** — a hand-entered "accept value" correction (distinct from "accept
  candidate/whistleblower," which just attributes the win to the real provider and archives
  nothing new) is written directly here, bypassing staging entirely, unconditionally tagged
  `provider_id` = `'manual'` even if the value happens to match an existing provider's own
  reported number. **Not yet implemented** — the CLI/API surface for this
  (`tasks/finalize_targeted_promotion.md`) is a separate, not-yet-converged follow-up; this
  migration only adds the schema (table, plus `dim_provider`'s `'manual'` row) it depends on.

Loosely mirrors `staging_market_data_1min`'s columns rather than the leanest possible shape,
since new columns are expected here over time and a close mirror keeps that trivial. The surrogate
`archive_id` primary key (rather than the natural `(provider_id, ticker_id, date_id, time_id)` key)
exists because `fact_reconciliation_participant.archive_id` must be able to reference a `'manual'`
row with no corresponding staging row to key off of, and because the natural key isn't unique here
— the same bar can be archived more than once over time (e.g. re-ingested and re-resolved after a
prior archival). Never pruned — retention is perpetual, by design (a same-instance tablespace move
to cheaper storage remains available later with zero schema impact if the table ever outgrows
CroicuWS1's ~10TB, but that's a physical relocation, not a row-count/retention policy).

## `dim_field`

| Column | Type | Notes |
|---|---|---|
| `field_id` | `SERIAL PRIMARY KEY` | |
| `name` | `TEXT NOT NULL UNIQUE` | Always lowercase — enforced by a `CHECK` constraint. `'open'`/`'high'`/`'low'`/`'close'` |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

Added in `007_add_dim_field_and_dataset_inception`, mirroring `dim_provider`'s shape. Distinct from
`dim_field_group`: `dim_field_group` still governs which `fact_market_data_1min` columns
`quant-reconcile` must promote together as one atomic unit (OHLC from a single winning provider —
unchanged by this migration); `dim_field` exists solely so `provider_pair_disagreement` can measure
each field's tolerance independently. `yfinance`'s noise concentrates in `high`/`low` while
`open`/`close` stay comparatively stable — a single `'ohlc'`-group tolerance let the noisy fields
set (or blow) the band for the stable ones. `provider_pair_disagreement` is currently the only
consumer.

## `provider_pair_disagreement`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` — always a `role = 'candidate'` provider, never the whistleblower |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker`. Added in `007_add_dim_field_and_dataset_inception`, replacing the table's ticker-agnostic pooling |
| `field_id` | `INT NOT NULL` | FK → `dim_field`. Added in `007_add_dim_field_and_dataset_inception`, replacing `field_group_id` |
| `sample_count` | `BIGINT NOT NULL DEFAULT 0` | `>= 0` |
| `running_mean` | `NUMERIC NOT NULL DEFAULT 0` | Signed: `candidate_value - whistleblower_value`, not the reverse |
| `running_m2` | `NUMERIC NOT NULL DEFAULT 0` | Welford's algorithm accumulator |
| `stddev` | `NUMERIC` | Denormalized from `running_m2`/`sample_count` for fast reads |
| `updated_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(provider_id, ticker_id, field_id)`. Added in `004_add_reconciliation_tables`,
re-keyed in `007_add_dim_field_and_dataset_inception`, consumed by `quant-reconcile`'s per-field
Tier 2/3 tolerance and per-ticker graduation gate (see
[croicu/quant-data#28](https://github.com/croicu/quant-data/issues/28)/#29 and
`docs/ARCHITECTURE.md`'s `reconcile` section). Running variance of each candidate
provider's disagreement against the fixed whistleblower (`yfinance` today), per ticker per field —
measured directly rather than reconstructed from two individually-unmeasurable per-provider
"precision" figures (no ground-truth reference exists to attribute disagreement to one side or the
other). Keyed per-ticker and per-field, not pooled, because noise isn't homogeneous across either
dimension: `DOG`'s real disagreement band is wider than `SPY`/`QQQ`/`DIA`'s (traced to one global
`stddev` dominated by the tight-agreement tickers), and `yfinance`'s noise concentrates in
`high`/`low` while `open`/`close` stay comparatively stable (traced to one `'ohlc'`-group tolerance
letting the noisy fields set the band for the stable ones). `stddev` is relative/fractional, scaled
against an actual reference value at comparison time to get `quant-reconcile`'s bar-specific
absolute tolerance (`tolerance = k * stddev * reference_value`, `k = 3`). Only in-band
(raw-agreement) observations update this — `finalized`/`manual_override`/`unadjudicated`
resolutions are excluded so outliers can't gradually widen "normal" (`'unadjudicated'` in
particular never compared a candidate against a reference value at all, so it carries no
information about disagreement one way or the other). `007` discards the table's pre-existing
pooled rows rather than migrating them forward — every `(provider, ticker, field)` starts at zero
and earns its own real history; no seed value this time (contrast with `004`'s illustrative seed),
since a ticker's first real stats computation only ever happens over a full batch of
actually-observed matched bars (see the per-ticker graduation design in
[croicu/quant-data#28](https://github.com/croicu/quant-data/issues/28)), so there's no cold-start
gap to seed in the first place. `croicu/quant-data#44` widened graduation from a per-ticker gate to
per-`(candidate, ticker, field)`, so a second candidate (`massive`) added to an already-graduated
ticker still reaches its own graduation batch instead of being permanently locked out.

## `data_quality_thresholds`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `k_reversal_oc` | `NUMERIC NOT NULL` | MAD multiplier for `open`/`close`, reversal-shaped diffs (tight) |
| `k_trend_oc` | `NUMERIC NOT NULL` | MAD multiplier for `open`/`close`, trend-shaped diffs (loose) |
| `k_reversal_hl` | `NUMERIC NOT NULL` | MAD multiplier for `high`/`low`, reversal-shaped diffs (tight, but looser than OC) |
| `k_trend_hl` | `NUMERIC NOT NULL` | MAD multiplier for `high`/`low`, trend-shaped diffs (loose, but looser than OC) |
| `updated_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(provider_id, ticker_id)`. Added in `010_add_data_quality_thresholds`
([croicu/quant-data#32](https://github.com/croicu/quant-data/issues/32)), no rows seeded — a
missing `(provider, ticker)` falls back to `reconcile/outlier_detection.py`'s own
`DEFAULT_K_*` constants (seed values `3`/`6`/`4`/`8` from the 2026-08-06 design session, not yet
validated). Deliberately separate from `provider_pair_disagreement`: that table measures
*cross-provider* disagreement (candidate vs. whistleblower) to set reconciliation tolerance; this
one holds *intra-provider* plausibility coefficients (is a value implausible relative to its own
series' recent neighbors) for the outlier-detection check that sets `data_quality = 'rejected'` —
related concepts, deliberately not unified into one table. Only ever holds deliberately-tuned
overrides, so this table can (and likely will) stay empty for a long time.

## `materiality_floor`

| Column | Type | Notes |
|---|---|---|
| `provider_id` | `INT NOT NULL` | FK → `dim_provider` |
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `field_id` | `INT NOT NULL` | FK → `dim_field` |
| `floor_value` | `NUMERIC NOT NULL` | Interpretation depends on `floor_type` |
| `floor_type` | `TEXT NOT NULL CHECK (floor_type IN ('absolute', 'bps_of_reference'))` | `absolute`: `floor_value` is a raw unit (e.g. one tick). `bps_of_reference`: `floor_value` is basis points of the bar's own reference value |
| `updated_at` | `TIMESTAMP` | Defaults to insert time |

Primary key: `(provider_id, ticker_id, field_id)` — deliberately the exact same grain as
`provider_pair_disagreement`, `provider_id` implicitly meaning the candidate (whistleblower stays
singular/implicit, same convention). Added in `013_add_materiality_floor`
(`tasks/materiality_floor_tolerance.md`); a missing `(provider, ticker, field)` falls back to
`floor_value = 0.0` (no floor).

`014_seed_materiality_floor_defaults` seeds today's 6 actively-ingested tickers with
`floor_type = 'bps_of_reference'` defaults derived from a real finding, not a gut prior: per-bar
`ibkr` volume correlates with `ibkr`/`yfinance` disagreement (log-log regression over the full
pending-manual-resolution backlog as of 2026-08-15, `R² = 0.32` — a real relationship, explaining
roughly a third of per-bar variance, not the whole story). Same value applied uniformly across
`open`/`high`/`low`/`close` per ticker (the regression was fit against each bar's worst-of-4-fields
diff — deliberately the conservative choice, not per-field-tuned). Explicitly overridable per
`(provider, ticker, field)` as more data accumulates — see `CLAUDE.md`'s Pending Tasks entry on the
volume/noise correlation for the full finding and its still-open tension with an earlier,
conflicting investigation into `DOG`'s stuck rate.

**`015_relax_materiality_floor_psq_dog`**: live validation of `014`'s defaults (full clean-slate
reconcile run, 2026-08-15) found the population regression under-predicted `PSQ` and `DOG` badly
enough to produce zero backlog reduction for either — `DOG`'s own observed average diff (5.017
bps) was more than double its seeded floor (2.193 bps); `PSQ`'s was triple (3.952 vs. 1.299).
Overridden with each ticker's own P90 (90th percentile of its pending backlog's diff distribution)
instead of the cross-ticker model — a deliberate precision-for-workload tradeoff: `PSQ`/`DOG`
matter more for trading execution than research-grade reconciliation accuracy. `IWM` deliberately
left unseeded (`floor_value = 0.0`, no floor) — only 12 pending bars to derive a P90 from (vs.
`PSQ`/`DOG`'s 31/37), too thin a sample to calibrate responsibly; revisit once more data
accumulates.

Bounds `quant-reconcile`'s Tier 2/3 tolerance (`k * stddev * reference_value`) below by an
economically meaningful minimum, so Tier 2 classification means "statistically out of band *and*
material enough to be worth a human's time" — not pure z-score distance. Without a floor, a field
whose true cross-provider variance is genuinely tiny sees its tolerance shrink right along with the
honestly-converging `stddev` estimate from `provider_pair_disagreement`, pushing economically
trivial disagreements to Tier 4 (manual) purely because they exceed an ever-tightening *relative*
threshold. Volume has no independent tolerance check to bound (rides along with the `ohlc` winner
since `005_remove_volume_field_group`), so this table is keyed to `dim_field`
(`open`/`high`/`low`/`close`), not `dim_field_group` — there'd be nothing for a volume row to
affect. Only consulted by the automatic pass (Tiers 1-3); `--finalize`'s `resolve_finalize` never
touches tolerance at all, so it's unaffected either way.

## `fact_pending_manual_resolution`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `field_group_id` | `INT NOT NULL` | FK → `dim_field_group` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC, same as `fact_market_data_1min`/`staging_market_data_1min`/`market_data_archive`. Added in `012_add_timestamp_to_pending_manual_resolution` |
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

`timestamp` (added in `012_add_timestamp_to_pending_manual_resolution`,
[croicu/quant-data#36](https://github.com/croicu/quant-data/issues/36)) brings this table in line
with every other fact/staging table, all of which carry a denormalized `timestamp` alongside their
`date_id`/`time_id` dimension keys — this was the one holdout, forcing a `dim_date`/`dim_time` join
just to get a readable timestamp. Surfaced building a Power Query/Excel dashboard against
`quant-data` from `quant-scratch`'s `open-quant-data` tool
([croicu/quant-scratch#19](https://github.com/croicu/quant-scratch/issues/19)/
[#20](https://github.com/croicu/quant-scratch/pull/20)).

## `dataset_inception`

| Column | Type | Notes |
|---|---|---|
| `id` | `INT PRIMARY KEY DEFAULT 1` | `CHECK (id = 1)` |
| `inception_date` | `DATE NOT NULL` | |

Added in `007_add_dim_field_and_dataset_inception`; read by `quant-ingest --backfill`
(`PostgresDatabase.fetch_dataset_inception_date`, see
[croicu/quant-data#28](https://github.com/croicu/quant-data/issues/28)/#29 and
`docs/ARCHITECTURE.md`'s `ingest` section) as the backward-walk's target. Single-row table
recording the date the dataset is meant to start from — a fact about the dataset's own properties,
not tunable process behavior, so it lives here rather than `settings.json`. The `CHECK (id = 1)`
enforces the single-row invariant at the DB level rather than relying on convention (contrast with
`schema_migrations`, which legitimately has one row per migration). **Still empty on the real
database as of #29** — `fetch_dataset_inception_date` raises `AppError` when no row exists, so
`--backfill` cannot actually run until a real value is inserted by hand (a manual, one-time data
decision outside this code's scope, not a bug). Moving `inception_date` backward once populated is
an operational trigger, not just a value update: `--backfill` must re-run for every configured
ticker from the new `inception_date` to that ticker's own current earliest covered date — not a
single shared date, since tickers may already have different amounts of history.

## `quant_ingest` database

A **separate Postgres database**, not a schema or table inside `quant_data` — same server/instance
(CroicuWS1), own connection (`settings.postgres.archiver.dbname`), own `schema_migrations`, own
migration sequence (`migrations/quant_ingest/*.sql`, independently numbered from `migrations/*.sql`).
Added by [croicu/quant-data#52](https://github.com/croicu/quant-data/issues/52) as the append-only
record of every provider fetch (see `provider_source_archive` below for exactly what "append-only"
does and doesn't guarantee at the privilege level), addressing two things `staging_market_data_1min`/
`market_data_archive` didn't: ingestion can be slow and cost real API calls, but nothing in
`quant_data` is actually immutable, and `market_data_archive` only ever captures a *candidate's*
staging row, only *once purged* — never the whistleblower, never at ingest time. Deliberately a
separate database, not just a table: Postgres has no cross-database foreign keys, so this repo's own
routine clean-slate testing (`DROP DATABASE quant_data`) can never touch it structurally — unlike
`market_data_archive`, which has been swept up in a "clean slate" `TRUNCATE` list at least once.
Because there's no cross-database FK, `ticker`/`provider` are stored as plain validated text here,
not surrogate-key references to `quant_data`'s `dim_ticker`/`dim_provider`.

### `provider_source_archive`

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `ticker` | `TEXT NOT NULL` | `CHECK (ticker = UPPER(ticker))`, mirroring `dim_ticker.ticker`'s own constraint |
| `provider` | `TEXT NOT NULL` | e.g. `'yfinance'`, `'ibkr'`, `'massive'` — no FK, `quant_ingest` has no `dim_provider` |
| `method` | `TEXT NOT NULL` | which provider call/endpoint produced this row, e.g. IBKR's `'TRADES'`/`'BID_ASK'`; single-valued for Massive (`'aggregates'`) and yfinance (`'history'`) — see below |
| `trading_date` | `DATE NOT NULL` | |
| `fetched_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `fetch_version` | `TEXT NOT NULL` | the fetching provider's `IntraDayProvider.FETCH_VERSION` at fetch time |
| `payload_kind` | `TEXT NOT NULL` | `CHECK (payload_kind IN ('raw_api_response', 'parsed_bars'))` |
| `payload` | `JSONB NOT NULL` | see below |

No unique constraint on `(ticker, provider, method, trading_date)` — append-only by design; a
re-fetch of the same day (a catch-up re-run, a backfill retry) is a new row, never an upsert.
`ProviderSourceArchiveWriter.record_fetch` only ever `INSERT`s; nothing in the application code
issues `UPDATE`/`DELETE` against this table. `quant_writer` originally had `INSERT` + sequence
`USAGE` only (true DB-enforced immutability); `DELETE` was granted afterward for manual cleanup, at
the repo owner's explicit, informed request (2026-08-17 — see `docs/DATABASE.md`). `UPDATE` remains
ungranted — a row can be removed, never edited in place.

`method` was added to the key by [croicu/quant-data#60](https://github.com/croicu/quant-data/issues/60)
(2026-08-18, `tasks/ingestion_layer_spec.md`), coalesced directly into the init migration rather than
a separate `ALTER` — no production archive data existed yet worth an incremental migration path.
A provider's blob is not self-describing on replay without knowing which call produced it: IBKR's
serialized `BarData` is ambiguous between `TRADES`/`BID_ASK`/`MIDPOINT` without this column. Plain
`TEXT`, not a `CHECK`-constrained enum — IBKR's set of methods is expected to grow. Sourced from
each `IntraDayProvider`'s own `METHOD` class attribute (mirrors `FETCH_VERSION`'s precedent),
itself set from `quant_data._internal.contracts.PRIMARY_METHOD_BY_PROVIDER` — the single source of
truth both `ingest` (via `provider.METHOD`) and `stage` (which has no provider objects, only
provider name strings) draw from.

`payload_kind` exists because "raw" isn't uniform across providers: `MassiveIntraDay`'s plain
`requests.get(...).json()` call genuinely has a raw JSON response to archive
(`payload_kind = 'raw_api_response'`) — but `YahooFinanceIntraDay` never sees raw JSON at all (the
`yfinance` package parses its own HTTP call internally) and `IBKRIntraDay` has no JSON to begin with
(IB Gateway/TWS's wire protocol via `ib_async` isn't JSON). Both instead get a JSON-serialized form
of the already-parsed `OHLCV` bars (`payload_kind = 'parsed_bars'`,
`quant_data._internal.shared.providers.payload.parsed_bars_payload`). `quant_data._internal.contracts
.PayloadKind`/`ProviderFetchResult` are the Python-side shapes behind this — see
`docs/ARCHITECTURE.md`.

`fetch_version` is deliberately a plain string, not numeric — a per-provider `FETCH_VERSION` class
attribute, bumped by hand whenever that provider's own request construction changes (a new
parameter, a changed default) in a way that could change what comes back. Lets a later pass identify
which archived ranges were fetched under an outdated query shape.

### `archive_coverage`

| Column | Type | Notes |
|---|---|---|
| `coverage_id` | `SERIAL PRIMARY KEY` | |
| `ticker` | `TEXT NOT NULL` | |
| `provider` | `TEXT NOT NULL` | |
| `method` | `TEXT NOT NULL` | see `provider_source_archive.method` above |
| `fetch_version` | `TEXT NOT NULL` | |
| `start_date` | `DATE NOT NULL` | |
| `end_date` | `DATE NOT NULL` | |
| `updated_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |

The archive-side equivalent of `quant_data.ingestion_coverage` (croicu/quant-data#31) — coalesced
contiguous date ranges, updated incrementally on every successful fetch rather than recomputed from
scratch — but keyed by `(ticker, provider, method, fetch_version)` instead of just
`(ticker, provider)`. `method` joined this key alongside `provider_source_archive`'s own
(croicu/quant-data#60): `BID_ASK` and `TRADES` can return different bar counts for the same window
(confirmed live), so coverage for one method says nothing reliable about coverage for another.
Unlike `provider_source_archive` (`INSERT`/`DELETE` only, no `UPDATE`), this table *is* fully
mutated (`quant_writer` has `SELECT`/`INSERT`/`UPDATE`/`DELETE`, same as `ingestion_coverage`) — it's
maintained summary state, not an archival record itself. A date not covered by *any*
`fetch_version`'s range for a `(ticker, provider, method)` is a genuine gap ("no IBKR TRADES data
two weeks ago"); a date covered only by a non-current `fetch_version` is stale and a candidate for
re-fetching in smaller chunks, without disturbing the old version's own range.
`ProviderSourceArchiveWriter.record_fetch` writes both tables in one transaction, so a fetch is
never archived without its coverage range also reflecting it.

## `quant_schedule` database

A **separate Postgres database**, not a schema or table inside `quant_data` — same server/instance,
own connection (`settings.postgres.worker`), own `schema_migrations`, own migration sequence
(`migrations/quant_schedule/*.sql`, independently numbered from `migrations/*.sql`). Added by
[croicu/quant-data#66](https://github.com/croicu/quant-data/issues/66) as the table-driven schedule
`quant-dispatch` reads. Deliberately its own database, not just a table: `jobs` is meant to
eventually schedule work against other databases this repo doesn't own too (a future
`quant_trades`, `quant_accounting`, ...), so it shouldn't be tied to `quant_data`'s own lifecycle —
this repo's routine clean-slate testing (`DROP DATABASE quant_data`) must never touch it. Reads/
writes go through `ScheduleDatabase` (`quant_data._internal.shared.schedule`), a class deliberately
separate from `PostgresDatabase`, same reasoning as `ProviderSourceArchiveWriter`/`Reader` for
`quant_ingest` above.

Its own role split, deliberately separate from `quant_writer`/`quant_reader`: `quant_scheduler`
(full CRUD on `jobs`/`job_dependencies` — `SELECT`, `INSERT`, `UPDATE`, `DELETE` — what
`quant-schedule` (croicu/quant-data#68) connects as via `settings.postgres.scheduler` to create and
manage a work item's job graph) and `quant_worker` (read/update on `jobs`, read-only on
`job_dependencies` — `fetch_due_jobs`'s dependency-gating query joins against it — what
`quant-dispatch` itself connects as via `settings.postgres.worker`; it never inserts a row). See
`docs/DATABASE.md`'s
"Setting up the `quant_schedule` database" section for the exact `CREATE ROLE`/`GRANT` statements.

### `jobs`

| Column | Type | Notes |
|---|---|---|
| `job_id` | `SERIAL PRIMARY KEY` | |
| `name` | `TEXT NOT NULL UNIQUE` | |
| `command` | `TEXT[] NOT NULL` | argv array, e.g. `{quant-ingest,--catch-up}` |
| `interval_seconds` | `INT NOT NULL` | `CHECK (interval_seconds > 0)` |
| `next_run_at` | `TIMESTAMP NOT NULL` | |
| `enabled` | `BOOLEAN NOT NULL DEFAULT true` | |
| `status` | `TEXT NOT NULL DEFAULT 'idle'` | `CHECK (status IN ('idle', 'running'))` |
| `last_run_at` | `TIMESTAMP` | |
| `last_exit_code` | `INT` | |
| `last_error` | `TEXT` | |
| `run_once` | `BOOLEAN NOT NULL DEFAULT false` | added in `002_add_dependencies_and_run_once` |

Added in `001_add_jobs_table` — a generic, table-driven schedule for the repo's recurring
processes (`quant-ingest`/`quant-stage`/`quant-reconcile`), reviving the postponed brainstorm at
`tasks/scheduled_jobs.md` (issue #3). Job definitions live here as data rather than committed code
specifically so the public repo never has to name a specific host — only the table shape and
generic dispatch code are checked in. **Ships empty** — real job rows (real intervals, real
`command` values, anything host-specific) are inserted programmatically by `quant-schedule`
(connecting as `quant_scheduler`, see below) or by hand via the same role, same precedent as
`quant_data.dataset_inception`.

`command` is passed straight to `subprocess.run` without `shell=True`, so there is no shell
injection/quoting concern from its contents. `status` guards against double-dispatch:
`quant-dispatch`'s `fetch_due_jobs` only considers `status = 'idle'` rows, and `mark_job_running`
flips a row to `'running'` immediately before its subprocess launches, so a `quant-dispatch`
invocation overlapping a still-running prior one skips that job rather than launching a second
concurrent instance (e.g. two `quant-reconcile` runs against the same database at once).
`record_job_result` always flips `status` back to `'idle'` regardless of `last_exit_code` — a
failed run is still a finished run, and `next_run_at` (computed by
`dispatch.algorithm.compute_next_run_at`, always `now + interval_seconds`, not the job's own
prior `next_run_at`, so a dispatcher that was down or delayed doesn't pile up a burst of
immediately-due catch-up runs on resume) still advances so the job is retried on schedule rather
than stuck. Exception: a `run_once` job that just succeeded is disabled (`enabled = false`)
instead of rescheduled — `record_job_result`'s `disable` parameter, set by `dispatch/cli.py`
exactly when `job.run_once` and the run succeeded; a `run_once` job that failed still
reschedules/retries normally.

`quant-dispatch` itself is one-shot, not a daemon: it checks `jobs` once, dispatches whatever's
due, and exits. The actual "run every minute" trigger (a cron entry or systemd timer) is a
host-level concern outside this repo, consistent with the rest of this table's design goal.

### `job_dependencies`

| Column | Type | Notes |
|---|---|---|
| `job_id` | `INT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE` | |
| `depends_on_job_id` | `INT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE` | |

`PRIMARY KEY (job_id, depends_on_job_id)`, `CHECK (job_id <> depends_on_job_id)`.

Added in `002_add_dependencies_and_run_once` (croicu/quant-data#68). `001`'s original design
deliberately left job *ordering* (e.g. `ingest` before `stage` before `reconcile`) to each job's
own `interval_seconds`/`next_run_at` offset rather than a `depends_on`/DAG mechanism — an
accepted risk for recurring jobs, mitigated by `ingest`/`stage` being idempotent. `quant-schedule`
(a one-shot backfill breaker) doesn't get that same self-healing from retries, so real dependency
tracking was added for it specifically: `fetch_due_jobs`'s query excludes any job with a row here
whose `depends_on_job_id` points to a job that hasn't yet succeeded (`last_exit_code IS NULL OR
<> 0`). A gated job is simply omitted that dispatch cycle — its own `next_run_at` doesn't move, so
it's re-considered on every later invocation with no separate bookkeeping needed. Populated only
by `WorkItemScheduleWriter.create_jobs` (`quant-schedule`, connecting as `quant_scheduler`), never
by `quant-dispatch` itself.

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
- `idx_market_data_archive_ticker_date_time` on `market_data_archive(ticker_id, date_id, time_id)`
  — same rationale as `staging_market_data_1min`'s own secondary index: supports "everything ever
  archived for this bar" lookups, the natural read pattern for an audit table keyed by a surrogate
  `archive_id`.
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
