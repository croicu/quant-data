# IBKR Provider Reconciliation

## Status: Brainstorm (schema-only slice done — issue #18, ready-to-submit; IBKR `IntraDayProvider`
slice done — issue #21, ready-to-submit)

## Problem statement

An IBKR account now exists, and the intent is to add IBKR as a second `IntraDayProvider` alongside
the current Yahoo Finance source (`docs/ARCHITECTURE.md` already flagged IBKR as the eventually-
intended intraday source — this is that work, arriving earlier than a full swap).

Yahoo Finance and IBKR won't necessarily agree bar-for-bar (different data vendors, different
handling of pre-market/after-hours, different rounding). Simply overwriting one provider's data
with the other's — or picking a single "winner" provider outright — throws away the ability to
detect and understand those discrepancies before trusting either source. The actual goal is to let
both providers write independently, then reconcile them into a single trusted ("golden") dataset,
with visibility into where and how often they disagree, since there isn't yet enough real-world
data to know how reliable either provider actually is.

## Design decisions

- **IBKR's real and paper accounts are the same `dim_provider` row.** Both return identical market
  data for research purposes — the account used is an execution-environment detail (which
  credentials/endpoint `IntraDayProvider` connects with), not a distinct data identity. A single
  `'ibkr'` row covers both; `'yfinance'` is the other row today.
- **Schema-only first slice, split out from the rest of this brainstorm**: `dim_provider` +
  `staging_market_data_1min` are being migrated now, as a precursor to writing the IBKR
  `IntraDayProvider` itself — tracked as its own narrower issue (see "Implementation plan" below),
  the same way `quant-ingest --catch-up` was split out of the broader scheduled-jobs brainstorm.
  Everything else here (tolerance config, manual resolution UI, reputation table, the actual
  reconciliation logic) stays Brainstorm/deferred; this migration doesn't implement any of it, only
  the two tables it'll eventually need.
- **`dim_provider`** is a new dimension table (alongside `dim_ticker`/`dim_date`/`dim_time`),
  deliberately not hardcoded to exactly two rows — more providers may be added later, and the
  design shouldn't need revisiting when that happens. Shape mirrors `dim_ticker` exactly:
  `provider_id SERIAL PRIMARY KEY`, `name TEXT NOT NULL UNIQUE` (lowercase, `'yfinance'`/`'ibkr'`
  seeded directly in the migration), `created_at TIMESTAMP DEFAULT now()`.
- **A new staging table**, `staging_market_data_1min`, holds each provider's raw, as-ingested bars
  — same bar columns as `fact_market_data_1min` (open/high/low/close/volume/timestamp/incomplete)
  plus `provider_id`. Primary key `(provider_id, ticker_id, date_id, time_id)`. Each provider's
  `IntraDayProvider` implementation writes here independently, blind to what other providers wrote
  for the same bar.
- **`fact_market_data_1min`'s existing schema, grain, and public contract are unchanged.** This was
  a deliberate choice over adding `provider_id` to the fact table's own primary key: that would
  double storage for every reconciled bar, and would be a breaking change to `MarketData
  .fetch_bars` (the actual public read contract per `docs/ARCHITECTURE.md`), requiring a
  cross-repo announcement issue to `quant-scratch` per `CLAUDE.md`'s placement rule. Keeping
  reconciliation's *output* shaped exactly like today's fact table avoids all of that — only the
  *input* side (staging) needs the new dimension.
- **Reconciliation is folded into the existing `--catch-up` flow**, not a separate scheduled job.
  `--catch-up` already runs nightly and already tolerates partial/incomplete per-(ticker, date)
  state by design (see `docs/PROTOCOL.md`/`docs/ARCHITECTURE.md`), making it the natural home for
  a "fetch from every configured provider, then reconcile whatever's in staging" step, rather than
  introducing a second unattended job to operate.
- **Per-bar reconciliation logic**:
  1. If any *currently configured* provider hasn't yet written a staging row for a given bar, that
     bar stays in staging in a `settlement` stage — it is **not** promoted to golden on partial
     data, even if every provider that *has* reported so far agrees. (Rationale: a provider that
     reports late or a new provider added later could still introduce a real disagreement; treating
     "not all providers in yet" the same as "all providers agree" would silently miss that.)
  2. Once every configured provider has reported for that bar, compare per field
     (open/high/low/close/volume independently) against a **configurable-per-field tolerance**
     (not a single global tolerance, and not hardcoded per provider-pair — one tolerance value per
     field, reused across whatever providers are being compared). Volume can be held to exact-match
     simply by setting its tolerance to `0` — no separate code path needed for "some fields are
     exact, some aren't."
  3. All fields within tolerance → upsert the bar into `fact_market_data_1min` (golden, unchanged
     schema) and purge that bar's staging rows across all providers.
  4. Any field outside tolerance → bar stays in `settlement`, not purged, not yet golden.
- **`settlement` resolution is manual for V1.** No automatic tiebreaker (majority vote, prefer-IBKR,
  etc.) is implemented yet — deliberately deferred, same pattern this repo already uses for
  IBKR-as-real-source and exit-code error classification (see `CLAUDE.md`'s Pending Tasks): there
  isn't yet enough real disagreement data to know what tiebreaker logic would even be correct.
- **Provider reputation is tracked, not yet consulted.** When a person manually resolves a
  `settlement`-stage bar/day, that resolution is what updates the losing provider's reputation —
  a bare disagreement between two providers doesn't by itself indicate which one was wrong, so
  reputation only moves off an actual resolution event (manual now; possibly automatic later if a
  third provider breaks a tie). Reputation is recorded as trend-able history (not a single mutable
  score that can't be audited later), but does **not** yet feed back into reconciliation as an
  auto-preference signal — that's a deliberately deferred follow-on once there's a real reputation
  signal to trust.
- **Staging rows are purged once a bar reconciles into golden.** Nothing is retained in staging for
  bars that made it to `fact_market_data_1min` — the golden table itself is the retained record;
  keeping staging duplicates around indefinitely was considered and rejected as unnecessary storage
  with no clear consumer.

## Open questions

Resolved for the schema-only slice (see "Implementation plan" below):

- ~~Staging table indexing~~ — primary key stays `(provider_id, ticker_id, date_id, time_id)`
  (matches how each `IntraDayProvider` writes its own rows independently), plus a new secondary
  index on `(ticker_id, date_id, time_id)` to support reconciliation's actual access pattern:
  gathering every provider's row for one bar, which is the opposite leading-column order from the
  primary key.
- ~~Migration numbering~~ — `003_add_dim_provider_and_staging.sql`, covering both new tables
  together (they're introduced as a pair; nothing consumes `dim_provider` without
  `staging_market_data_1min` existing too). A reputation-table migration, if that design converges
  later, gets its own later number.
- ~~Cross-repo impact confirmation~~ — confirmed no `quant-scratch`-facing issue needed for this
  migration specifically: `fact_market_data_1min`'s schema/grain, `MarketData.fetch_bars`, and
  `create_postgres_provider` are all untouched. A provenance column on the fact table (discussed,
  not committed to) would reopen this question if it's ever actually added.

Still open — belong to the reconciliation-logic implementation, a later task, not this migration:

- **Tolerance configuration shape**: where do per-field tolerances live — `settings.json` (like
  `catchUpLookbackDays`), a new small config file, or a DB table (so they're adjustable without a
  restart)? Given they're explicitly meant to be tuned as real disagreement data comes in, a DB
  table may be more convenient than `settings.json`, but that's not decided.
- **Manual resolution mechanism**: how does a person actually resolve a `settlement`-stage bar —
  a new CLI subcommand/flag, direct SQL against staging, or something else? Needs a real interface,
  not just "a person decides."
- **Reputation table shape**: an event/history table (`provider_reputation_events` or similar) is
  favored over a single mutable score column, but the exact schema (what triggers an event, what
  it records beyond "provider X, direction, timestamp") isn't designed yet.
- **"Currently configured providers" scope**: is the provider list global (one list applies to
  every ticker), or could it vary per ticker (e.g. IBKR covers a ticker Yahoo doesn't, or vice
  versa)? Affects how reconciliation decides "all expected providers have reported" for a given bar.

## Implementation plan

**Schema-only slice** (this pass — no Python code, since nothing consumes these tables yet; the
IBKR `IntraDayProvider` and reconciliation logic are separate, later work):

1. `migrations/003_add_dim_provider_and_staging.sql`: `CREATE TABLE dim_provider` (seeded with
   `'yfinance'`, `'ibkr'`), `CREATE TABLE staging_market_data_1min`, plus the secondary index noted
   above. Wrapped in `BEGIN`/`COMMIT`, records itself in `schema_migrations`, matching
   `001`/`002`'s existing style.
2. `docs/SCHEMA.md`: document both new tables, their columns, and the new index, alongside the
   existing three-dimension/one-fact description.
3. Apply the migration against the real CroicuWS1 database via `psql` — **requires explicit
   go-ahead first**, per this repo's rule to confirm before running a migration against the real
   database.
4. Open a GitHub issue scoped to just this slice (schema only), labeled `status:implementation`,
   cross-linking back to this task file for the broader reconciliation context — same pattern as
   issue #12 (`--catch-up`) was split out of `tasks/scheduled_jobs.md`.

## Implementation plan (IBKR `IntraDayProvider` slice, issue #21)

Scoped narrower than this file's full reconciliation design, same pattern as #18's schema-only
slice: build the fetch-only provider, leave staging writes/reconciliation for a later issue.

- **Fetch-only, not wired into `ingest`.** `IBKRIntraDay` (`providers/ibkr.py`) implements
  `IntraDayProvider.fetch_bars`, symmetric to `YahooFinanceIntraDay`, covered by unit tests plus a
  live integration test against a real IB Gateway. `ingest/cli.py` still only constructs
  `YahooFinanceIntraDay` by default — running both providers per `--catch-up` and writing to
  `staging_market_data_1min` is deferred to whichever issue actually wires up reconciliation
  (needs the still-open questions below resolved first: tolerance config shape, "which providers
  are currently configured," etc.).
- **Long-lived connection across a batch**, not connect-per-call like `quant-scratch`'s validated
  approach. `connect()`/`close()` are explicit and separate from `fetch_bars()`, since IBKR's
  connection handshake is expensive enough to amortize across many (ticker, date) fetches once
  this is wired into a batch ingest run — `fetch_bars()` raises `AppError` if called before
  `connect()`.
- **`fetchFields=StartupFetchNONE`** on `connect()` skips `ib_async`'s default positions/orders/
  account-updates fetch, which a Read-Only-API Gateway (the correct setting for data-only ingest)
  otherwise rejects — the ~10s-per-connection quirk `quant-scratch` already found and fixed.
- **No zero-volume-as-incomplete heuristic** (unlike `YahooFinanceIntraDay`): IBKR only returns
  bars it actually has trade data for, so a zero-volume bar is a real "no trades that minute"
  fact, not a synthesized placeholder.
- **Ingest-scale pacing** (IBKR's documented 60 requests/10 minutes ceiling) was flagged by
  `quant-scratch` as worth a real test at batch scale — still untested here too, since this slice
  doesn't wire into a real batch run yet. Revisit once `ingest` actually drives this provider
  across many tickers/dates.

## Test results

**Schema-only slice (issue #18): done.** Applied to the real CroicuWS1 database via `psql` as
`quant_data`; verified independently, read-only, via `quant_reader` — 7 tables present
(`dim_provider`, `staging_market_data_1min` alongside the original five), `dim_provider` seeded
with `yfinance`/`ibkr`, `staging_market_data_1min`'s columns/PK/index/FKs all match the design. No
automated tests (no Python code changed).

**IBKR `IntraDayProvider` slice (issue #21): done.** 11 unit tests (mocked `ib_async`) covering
connect/close lifecycle, the not-connected/unqualified-contract/no-bars/provider-exception error
paths, and OHLCV mapping (including the no-incomplete-heuristic behavior). Live integration test
(`tests/integration/test_ibkr.py`) run against a real local IB Gateway (paper, port 4002) —
fetched 960 real 1-minute bars for SPY on 2026-07-31, only 40/960 zero-volume, versus the
practically-all-zero-volume premarket gap that motivated this work
(`Yahoo`: 315/315 zero-volume premarket bars for the same symbol/date, per issue #21). `ruff
format`/`ruff check` clean, full `pytest` suite (88 tests) passes — though
`tests/integration/test_ibkr.py` specifically requires a locally running IB Gateway/TWS at
`127.0.0.1:4002` to pass, unlike the Yahoo integration test which only needs network access.

Rest of this brainstorm (tolerance config, manual resolution mechanism, reputation table, wiring
both providers into `--catch-up` + staging, and the reconciliation logic itself) remains open —
this file stays as the working document for that, not deleted, the same way
`tasks/scheduled_jobs.md` survived `--catch-up` (#12) closing.