# Re-evaluate Existing `unadjudicated` Bars

## Status: Implementation

## Problem statement

`tasks/retroactive_revision.md`/[issue #85](https://github.com/croicu/quant-data/issues/85)
(closed, merged, live-verified) deliberately scoped the historical MAD band to
**going-forward only** — new `quant-reconcile` runs get the check; the 6,736 SPY bars
already promoted via blind `resolution_path = 'unadjudicated'` (zero cross-provider
check at all) were left untouched, with re-evaluating them explicitly deferred as "a
separate, riskier write-path question."

That mechanism is now proven forward (live-verified: 3,099 bars correctly resolved via
`historical_mad_agreement`, 101 correctly held back on genuine disagreement). This task
is that deferred follow-up: give the 6,736 existing SPY `unadjudicated` bars (and
whatever the equivalent count is for any other ticker, once seeded) the same real check,
retroactively.

**Checked live before any design work, 2026-08-26**: the underlying staging rows for
these bars are gone (0 of 6,736 still have both candidates present in
`staging_market_data_1min` — already purged). But **both providers' original archived
values are recoverable from `market_data_archive`** (confirmed directly: a sample bar has
both `ibkr` and `massive` rows archived with their original OHLC). So re-evaluation is
technically possible, just needs to read from `market_data_archive` instead of staging —
a different code path than plain `quant-reconcile`.

*(Side observation, not investigated further, probably unrelated: the same sample bar
showed two archive rows per provider — one from 2026-08-21, one freshly written by the
prior task's live verification run, with identical values. Possibly a duplicate/
re-archive on an already-purged bar, possibly an artifact of the frozen dataset's
history. Not chased further here — flag if it turns out to matter.)*

## Design decisions

**No retraction — relabel agreements only, flag disagreements without touching
`fact_market_data_1min`** (repo owner's explicit call, 2026-08-26, chosen over building a
retraction mechanism). Concretely, using `resolution_path` itself as the flag — no new
table needed:

- **Re-evaluate every `resolution_path = 'unadjudicated'` bar** for a ticker with a
  fully-seeded `candidate_pair_mad_band`, pulling both providers' original values from
  `market_data_archive` (staging is already purged for all of them — confirmed live).
- **Agrees within the band** → `UPDATE fact_reconciliation SET resolution_path =
  'historical_mad_agreement'` for that row. `fact_market_data_1min`'s actual OHLC value
  is untouched — it was already `preferredProvider`'s value either way, so this is a pure
  provenance/confidence relabel, not a data change. Same label the forward-looking
  mechanism already uses, so a reader can no longer tell (or need to care) whether a
  `historical_mad_agreement` bar was checked at promotion time or retroactively.
- **Disagrees beyond the band** → relabel to `'unadjudicated_disputed'`.
  `fact_market_data_1min` stays untouched (no retraction), but the bar is now
  distinguishable from a bar that was simply never checked — a future audit/manual-review
  pass can query `WHERE resolution_path = 'unadjudicated_disputed'` directly, no new
  table required.
- **No `fact_pending_manual_resolution` involvement** — that table's whole mechanism
  (`--finalize` resolving from live staging rows) doesn't apply here anyway, since
  staging is already purged for every one of these bars.
- **New `quant-reconcile --reevaluate-unadjudicated` flag** (not a separate script) —
  reuses the existing CLI's settings/connection/logging plumbing, mutually exclusive with
  `--finalize`. A one-off backlog pass, not part of the routine plain/`--finalize`
  cadence.
- **Multi-ticker**: processes whatever's seeded in `candidate_pair_mad_band` at run time,
  same as the forward-looking mechanism — no special-casing, SPY today, others
  automatically once seeded.

## Implementation plan

1. `src/reconcile/algorithm.py`: extracted `_candidate_pair_agrees_within_mad_band` out of
   `_resolve_historical_mad_agreement` so both the live tier and the new re-evaluation
   path share the byte-identical comparison. New `RESOLUTION_UNADJUDICATED_DISPUTED`
   constant and `reevaluate_unadjudicated(archived_candidates, field_group, mad_bands) ->
   str | None` pure function.
2. `src/quant_data/_internal/shared/postgres.py`: `ArchivedCandidateBarRow` dataclass,
   `fetch_archived_candidate_values_for_unadjudicated_bars(ticker_id)` (joins
   `fact_reconciliation` to `market_data_archive`, `DISTINCT ON` picks the
   most-recently-archived row per (bar, provider) — handles the duplicate-archive-row
   observation above defensively), `update_unadjudicated_resolution_paths_batch(updates)`
   (bulk `UPDATE`, one commit, never touches `winning_provider_id`).
3. `src/reconcile/cli.py`: extracted `_fetch_mad_bands_by_ticker` (shared with
   `_run_automatic_pass`, was previously inlined only there). New
   `_run_reevaluate_unadjudicated_pass`, `--reevaluate-unadjudicated` CLI flag
   (`argparse` mutually-exclusive group with `--finalize`), `run_reconciliation` gained an
   optional `reevaluate_unadjudicated_bars` parameter defaulting to `False`.
4. `migrations/021_add_unadjudicated_disputed_resolution_path.sql`: widens
   `fact_reconciliation.resolution_path`'s `CHECK`. **Not yet applied to any real
   database.**
5. `docs/SCHEMA.md`/`docs/PROTOCOL.md`/`CLAUDE.md` updated.
6. Tests: `tests/unit/test_reconcile_algorithm.py` (4 new pure-function tests for
   `reevaluate_unadjudicated`) and `tests/unit/test_reconcile_cli.py` (2 `parse_args`
   tests + 3 end-to-end tests through `FakeReconcileDatabase`, which gained
   `market_data_archive` support and the two new fetch/update methods).

## Test results

`ruff format`/`ruff check` clean. `pytest`: 407 passed (was 398 before this task; +9 new).
No live database writes made by this task's own work — `market_data_archive`'s
duplicate-row observation and the underlying 6,736-bar count were both read-only queries
from the prior task. Migration 021 is unapplied.
