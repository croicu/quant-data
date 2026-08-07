# Staging Archive Before Purge

## Status: Brainstorm

## Problem statement

`purge_staging_bar` deletes a bar's `candidate` (`ibkr`) staging rows once resolved and no longer
needed as a Tier-3 neighbor — but nothing preserves that raw data first. This violates the "No
information loss during the data processing stage" principle in `CLAUDE.md`'s Data Pipeline
Principles section: `fact_market_data_1min`/`fact_reconciliation` are supposed to stay re-creatable
from `staging_market_data_1min` alone, but once a candidate row is purged, that's no longer true for
its bar.

Concrete evidence (2026-08-07, investigating whether `quant-reconcile` could be re-run against a
"fresh" dataset without re-ingesting): of 52,953 resolved `fact_market_data_1min` bars, only 4,101
still had *both* providers' original staging rows intact. 41,913 had only one provider's row
surviving (almost always the `whistleblower`, since `yfinance` rows are permanently purge-exempt —
the `candidate` side was gone). 6,939 had *nothing* left in staging at all. Reconstructing a
"pristine, just-ingested" dataset for testing required restoring `fact_market_data_1min` from a
backup and re-deriving `candidate` staging rows from the winning value — which only recovers the
*winner's* value, never the actual `ibkr`-vs-`yfinance` comparison that originally happened. The
real disagreement evidence is just gone for the vast majority of already-resolved history.

## Design decisions

- **Chosen direction: archive instead of delete (not "stop purging").** Two options were
  considered:
  1. Archive a candidate row to a separate, never-pruned table immediately before
     `purge_staging_bar` deletes it from the working `staging_market_data_1min` table. Keeps the
     working table lean (so `_run_outlier_detection_pass`/`fetch_staging_rows_for_reconciliation`'s
     full-table sweep every run — deliberate, see `tasks/yahoo_data_sanitization.md` — stays fast)
     while making the pipeline genuinely reproducible.
  2. Just stop purging candidate rows at all. Simpler, and given this project's current scale (a
     handful of tickers, ~100K rows after weeks of data) might not bite for a long time — but both
     of the sweep-everything call sites above would grow unboundedly and slow down every future run
     as a direct consequence, not just grow disk usage.
  - Picked (1): keeps the existing full-sweep performance characteristics intact rather than
    trading them away for simplicity.

## Open questions

- **Archive table schema** — mirror `staging_market_data_1min`'s columns exactly, or something
  leaner (this is a write-once/read-rarely audit log, not a working table)?
- **Where archiving happens** — inline in `purge_staging_bar` (archive-then-delete as one unit), or
  a separate step? Does this need a new `PostgresDatabase` method, or does `purge_staging_bar`'s
  existing SQL just grow an `INSERT ... SELECT` before its `DELETE`?
- **Does the whistleblower side need archiving too?** It's already permanently purge-exempt today
  (never deleted), so arguably no — but worth confirming that stays true rather than silently
  assuming it, especially if this task ever changes the whistleblower exemption itself.
- **Read access** — does `quant_reader` need a grant on the new archive table, or is this
  write-only/`quant_data`-only for now (no public API surface planned yet)?
- **Retention of the archive table itself** — presumably never pruned (that's the whole point), but
  worth being explicit rather than assumed.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
