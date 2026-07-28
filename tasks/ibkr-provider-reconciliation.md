# IBKR Provider Reconciliation

## Status: Brainstorm

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

- **`dim_provider`** is a new dimension table (alongside `dim_ticker`/`dim_date`/`dim_time`),
  deliberately not hardcoded to exactly two rows — more providers may be added later, and the
  design shouldn't need revisiting when that happens.
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
- **Staging table indexing**: `fact_market_data_1min` has indexes tuned for point lookups and
  ticker/date range scans (see `docs/SCHEMA.md`); staging's access pattern (write-heavy, short-
  lived, scanned by the reconciliation job rather than by external readers) may not need the same
  indexes — not yet worked out.
- **Migration numbering**: this needs at least one new migration (`003_...`) for `dim_provider` +
  `staging_market_data_1min`, and a second for whatever reputation ends up being — not yet split
  out or drafted.
- **Cross-repo impact confirmation**: current design intentionally leaves `fact_market_data_1min`'s
  schema/grain/contract untouched, so per `CLAUDE.md`'s placement rule this shouldn't need a
  `quant-scratch`-facing issue — worth explicitly re-confirming once the migration is drafted, in
  case something (e.g. a new nullable `resolved_from_provider_id` provenance column on the fact
  table, discussed but not committed to) ends up touching the public surface after all.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->