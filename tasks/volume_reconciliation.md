# Volume Reconciliation

## Status: Done — code committed as part of `quant-reconcile`'s implementation (`f5b192f`, issue
#25; no separate issue of its own — see "Relationship to `quant-reconcile`'s CLI work" below).
Migration applied to the real database (2026-08-03) and live-verified — see Test results.

## Problem statement

`tasks/quant_reconcile.md`'s design (schema #24, closed; CLI now implemented and committed — see
that file) treats `volume` as its own independent field consistency group: it goes through
the same Tier 1-4 automatic pass as `ohlc` (completeness, raw agreement against `yfinance` within
a measured tolerance, boundary-fix, else stuck), with its own `provider_pair_disagreement` row and
its own `fact_reconciliation` rows, separate from whatever happens to `ohlc` for the same bar.

The first live test against a small real sample (`tasks/quant_reconcile.md`'s Test Results,
2026-08-03) showed this independence causing real friction: OHLC resolved 100% (294/294 bars)
while volume left 61/294 genuinely stuck even after stats convergence, sharply and consistently
ticker-dependent (QQQ/DIA ~43% stuck, SH/PSQ near 0%) in a pattern not yet explained. That result
prompted reconsidering whether volume should be independently reconciled against `yfinance` at
all, rather than continuing to tune the tolerance/tier logic to fit the observed disagreement.

Two reasons, from the person actually consuming this data, that volume shouldn't be an independent
consistency group in the first place:

1. **Volume isn't meaningful here as an absolute cross-provider-verified value.** It's used as a
   *relative* confidence signal — e.g. "50% of a normal session's volume" vs. "200%" — read
   together with a specific provider's own methodology, not as a number that needs independent
   corroboration from a second provider the way price does. Reconciling it against `yfinance` is
   solving a precision problem that doesn't actually matter for how the value gets used downstream.
2. **`yfinance` has no pre-market volume data at all**, and the trading window this project
   actually cares about is mostly the beginning of regular-hours trading — exactly adjacent to
   pre-market. For that window, the whistleblower isn't a weak signal, it's *no* signal — there's
   nothing for volume's Tier 2/3 comparison to check against in the period that matters most.
   `tasks/index_composite_check.md` is the leading candidate for a real, independent volume anomaly
   signal (composite-derived expected volume/behavior from an index ETF's constituents), separate
   from anything a two-provider whistleblower comparison could ever offer here.

## Design decisions

- **Volume is no longer an independently reconciled field group.** It stops going through
  `resolve_automatic`/`resolve_finalize`'s Tier 1-4 logic entirely — no tolerance check, no
  boundary-fix, no "stuck" state of its own, no `provider_pair_disagreement` tracking.
- **Volume simply follows whichever provider wins the `ohlc` group for that bar.** Once `ohlc`
  resolves (via completeness / agreement / boundary-fix / finalize / manual override, exactly as
  today), the winning provider's own `volume` value — already present on that same staging row —
  is what gets promoted to `fact_market_data_1min` alongside its OHLC fields. No separate lookup,
  no separate winner: a bar now promotes as soon as its (only remaining) group, `ohlc`, resolves.
- **`dim_field_group` keeps its shape but loses its `'volume'` row.** The table stays (it's still
  documented as extensible for a future field that genuinely does need independent-provider
  agreement), but going forward it only ever has an `'ohlc'` row. Chosen over collapsing the
  `field_group_id` dimension out of `fact_reconciliation`/`fact_reconciliation_participant`/
  `provider_pair_disagreement` entirely — that would be a bigger, riskier PK change to tables
  already applied against the real database, for a savings (dropping one now-unused column) that
  isn't worth the churn.
- **Existing volume-field-group data is deleted, not archived.** `fact_reconciliation`/
  `fact_reconciliation_participant` rows with `field_group_id` = volume (all from the small
  2026-08-03 live-test sample — nothing from a full production run yet), `provider_pair_disagreement`'s
  `ibkr`/`volume` row (still just the illustrative seed, never converged against a full dataset),
  and the `dim_field_group` row itself all get removed by a new migration. Nothing here is real
  production history worth preserving.
- **Reputation tracking (`fact_reconciliation_participant`) no longer fires for volume**, since
  volume no longer competes as its own group — consistent with "volume was never really being
  judged for correctness here" per reason 1 above.

## Open questions

None remaining:

- **Migration number/timing** — resolved: `migrations/005_remove_volume_field_group.sql` drafted
  (deletes volume rows from `fact_reconciliation_participant` → `fact_reconciliation` →
  `provider_pair_disagreement` → `dim_field_group`, in FK-safe order) and, with explicit go-ahead,
  applied against the real database on 2026-08-03.
- **Relationship to `quant-reconcile`'s CLI work** — resolved: this mechanism change shipped as
  part of `quant-reconcile`'s own commit (`f5b192f`, issue #25), not a separate follow-up. This
  task file exists to document the specific design/reasoning; it never got its own GitHub issue or
  commit.
- **Downstream provenance concern** — resolved, not a real issue: `fact_market_data_1min` has never
  exposed per-field provenance to consumers, so volume implicitly following the `ohlc` winner
  changes nothing observable from `quant_data.MarketData`'s side.

## Implementation plan

1. `src/reconcile/algorithm.py`: drop `FIELD_GROUP_VOLUME` and its `_GROUP_FIELDS` entry — only
   `FIELD_GROUP_OHLC` remains.
2. `src/reconcile/cli.py`'s `run_reconciliation`: promotion now reads `volume`/`incomplete`
   directly off the `ohlc` winner's own `StagingRow`, instead of separately looking up a volume
   winner via a second field-group resolution.
3. `migrations/005_remove_volume_field_group.sql`: drops the `'volume'` `dim_field_group` row and
   every reconciliation-table row keyed to it (see above) — applied to the real database.
4. Docs updated: `docs/SCHEMA.md` (`dim_field_group`/`fact_reconciliation`/
   `provider_pair_disagreement` sections), `docs/ARCHITECTURE.md` (`reconcile` section's promotion
   description), `docs/PROTOCOL.md` (`quant-reconcile`'s field-group description).
   `tasks/quant_reconcile.md` annotated with a "Superseded" section pointing here, rather than
   rewriting its historical design/test-results narrative in place.
5. Tests updated: `tests/unit/test_reconcile_algorithm.py` (dropped the volume-specific tier test),
   `tests/unit/test_reconcile_cli.py` (dropped `VOLUME`/`volume` field-group fixtures; adjusted
   `resolved`/`stuck` counts now that a bar has one field group, not two — most notably
   `test_large_disagreement_stays_stuck_until_finalize`, whose original premise was volume
   resolving independently *while* `ohlc` stayed stuck, which can no longer happen).

## Test results

`ruff format`/`ruff check` clean. `pytest`: 140 passed (141 minus the removed
`test_volume_group_uses_its_own_field_and_tolerance`; the one remaining failure,
`test_ibkr.py::test_fetch_bars_returns_live_intraday_data_for_known_ticker`, is the same
pre-existing environmental failure as before — no local IB Gateway running, not a regression).

**Live-verified against the local machine, 2026-08-03.** Migration applied (dropped the `'volume'`
`dim_field_group` row and its old rows in `fact_reconciliation`/`fact_reconciliation_participant`/
`provider_pair_disagreement`), the full 50,318-row `staging_market_data_1min` backup restored, then
pruned to a fresh 599-row sample (09:30-09:39 ET, the market open, all 6 tickers/5 sessions) and run through
`quant-reconcile`. Result: 299/299 groups resolved via `agreement`, 0 stuck, on a single
invocation — every bar's `fact_market_data_1min` row got its `volume` straight from the `ohlc`
winner's own staging row, with no separate volume tier logic involved at all. Full detail
(including the seeding-lag and lazy-purge fixes this same run exercised) in
`tasks/quant_reconcile.md`'s Test Results.
