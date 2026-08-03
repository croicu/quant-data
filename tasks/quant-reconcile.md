# quant-reconcile

## Status: Brainstorm

## Problem statement

`staging_market_data_1min` now fills up for real: every `quant-ingest` run writes each configured
provider's raw bars there independently (`yfinance` and `ibkr` today — see issue #22), and
`fact_market_data_1min` — the table `MarketData.fetch_bars` actually reads — no longer gets
written at all. Nothing yet reads staging back out and promotes agreeing bars into fact, so the
warehouse is currently accumulating provider data with no path to becoming the trusted, queryable
dataset it's meant to produce. `quant-reconcile` is that missing piece: a new CLI
(`src/reconcile/cli.py`, mirroring `src/ingest/`'s shape — console script only, no importable
surface, outside the `quant_data` namespace) that reads staging, compares each bar across the
providers that reported it, and promotes what agrees.

This file picks up where `tasks/ibkr-provider-reconciliation.md` (the original umbrella brainstorm
covering `dim_provider`/staging/IBKR-provider/reconciliation together) left off — that file's
schema (#18), IBKR provider (#21), and provider-wiring (#22) slices are all done; this is the
remaining, not-yet-built piece: reconciliation itself.

## Design decisions

Carried forward from `tasks/ibkr-provider-reconciliation.md`, already converged there:

- **Separate CLI, not folded into `--catch-up`.** Originally planned as a step inside
  `quant-ingest --catch-up`; decided during #22 to keep it wholly separate —
  `quant-ingest`'s job ends at "run every configured provider, write staging"; `quant-reconcile`'s
  job is "read staging, compare, promote to `fact_market_data_1min`." The two can be scheduled
  independently (e.g. ingest nightly, reconcile on a different cadence or manually).
- **Per-bar reconciliation logic**:
  1. If any *currently configured* provider (`settings.providers`, the same global list `ingest`
     uses) hasn't yet written a staging row for a given bar, that bar stays in staging in a
     `settlement` stage — **not** promoted on partial data, even if every provider that *has*
     reported so far agrees. (A provider that reports late, or a new provider added later, could
     still introduce a real disagreement; treating "not all providers in yet" the same as "all
     providers agree" would silently miss that.)
  2. Once every configured provider has reported for that bar, compare per field
     (open/high/low/close/volume independently) against a **configurable-per-field tolerance**
     (one tolerance value per field, reused across whatever providers are being compared — not
     hardcoded per provider-pair). Volume can be held to exact-match by setting its tolerance to
     `0` — no separate code path needed for "some fields are exact, some aren't."
  3. All fields within tolerance → upsert the bar into `fact_market_data_1min` (unchanged schema)
     and purge that bar's staging rows across all providers.
  4. Any field outside tolerance → bar stays in `settlement`, not purged, not yet golden.
- **`settlement` resolution is manual for V1.** No automatic tiebreaker (majority vote,
  prefer-IBKR, etc.) — deliberately deferred, same pattern this repo already uses for
  IBKR-as-real-source and exit-code error classification: there isn't yet enough real disagreement
  data to know what tiebreaker logic would even be correct.
- **Provider reputation is tracked, not yet consulted.** A person manually resolving a
  `settlement`-stage bar/day is what updates the losing provider's reputation — a bare
  disagreement between two providers doesn't by itself indicate which one was wrong, so reputation
  only moves off an actual resolution event. Recorded as trend-able history (not a single mutable
  score), but doesn't yet feed back into reconciliation as an auto-preference signal.
- **Staging rows are purged once a bar reconciles into fact.** Nothing is retained in staging for
  bars that made it to `fact_market_data_1min` — the fact table itself is the retained record.

## Open questions

These are the actual blockers on writing `quant-reconcile` — need real decisions, not just
options listed:

- **Tolerance configuration shape**: where do per-field tolerances live?
  - `settings.json`, alongside `catchUpLookbackDays` etc. — simplest, consistent with everything
    else `quant-reconcile` would read, but requires a process restart (or a fresh `Settings.load()`
    invocation, which a CLI run naturally does anyway) to change.
  - A new small config file dedicated to tolerances.
  - A DB table — adjustable without touching a file at all, and tolerances are explicitly expected
    to be tuned as real disagreement data comes in, which favors this. Adds a migration and a new
    read path `quant-reconcile` needs, though, for a single small config concern.
- **Manual resolution mechanism**: how does a person actually resolve a `settlement`-stage bar?
  - A new CLI subcommand/flag on `quant-reconcile` itself (e.g. `quant-reconcile --resolve ...`)
    that lets someone pick a winning provider (or supply a value) for a specific stuck bar/day.
  - Direct SQL against staging — no code, but no guardrails (nothing records *why* a bar was
    resolved a certain way, which the reputation design above depends on).
  - Something else (a small script, a read-only report + manual `psql`, ...). Needs a real
    interface either way, not just "a person decides" — whatever it is has to be the thing that
    actually writes the provider-reputation event this file's design decisions assume exists.
- **Reputation table shape**: an event/history table (`provider_reputation_events` or similar) is
  favored over a single mutable score column, but the exact schema isn't designed — what columns
  beyond "provider X, direction, timestamp"? Does it reference the specific bar/day that triggered
  it? Does the manual-resolution mechanism above need to exist first, since it's the only thing
  that would ever write a row here?

## Implementation plan

<!-- TBD -- blocked on the open questions above converging first. -->

## Test results

<!-- TBD -->
