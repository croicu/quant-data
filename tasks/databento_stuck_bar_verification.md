# DataBento Stuck-Bar Verification

## Status: Brainstorm

## Problem statement

Manual resolution of stuck bars (`fact_pending_manual_resolution`) today has no independent
third opinion — a person just has to judge `ibkr` vs. `yfinance` from candlestick shape and
context, which is slow and occasionally wrong on a first pass (see
`tasks/finalize_targeted_promotion.md`'s Problem statement: an initial read of the 3 pending
`SPY` bars judged `yfinance` correct, and a later re-look reversed that to `ibkr`).

DataBento was already used once as a tie-breaker for exactly this: paid 1-minute data pulled by
hand for `SPY` over the same 3-day window, decisively refuting `yfinance`'s outlier value on all
three bars, matching `ibkr` closely on two of three (a smaller, unresolved residual gap on the
third — see that task file for exact numbers). That file's explicit conclusion at the time: **not
adopting DataBento as an ongoing/routine reference** — a paid source, and that pull was scoped as
a one-off sanity check, not a new candidate provider.

**Proposed reframe**: an *optional* `quant-reconcile` flag that calls DataBento only for bars
already stuck in the pending queue — not a routine/blanket third data source, not a new
`dim_provider` candidate feeding automatic reconciliation. Narrower than what the prior "not
adopting as ongoing/routine" decision ruled out, but flag the tension explicitly for the DV
conversation rather than assuming it's already settled — the prior decision should be revisited
on purpose, not silently overridden by a differently-scoped feature that ends up doing the same
thing by another name.

## Design decisions

<!-- Not yet converged -- open questions below need resolving first. -->

## Open questions

- **Does this replace or assist manual judgment?** `tasks/ibkr-provider-reconciliation.md`'s
  existing design deliberately deferred any automatic tiebreaker ("settlement is manual for V1...
  there isn't yet enough real disagreement data to know what tiebreaker logic would even be
  correct"). Does a DataBento cross-check get to auto-promote a winner (a real tiebreaker,
  reopening that deferred decision), or does it just surface DataBento's value alongside the
  existing two for a person to use via `--finalize` (assistive only, no change to the "manual for
  V1" stance)?
- **Cost/budget**: DataBento is paid per-pull. An optional flag scoped to "only stuck bars" caps
  the blast radius somewhat, but the pending backlog is currently 127 bars (`tasks/
  finalize_targeted_promotion.md`) and growing — needs an explicit budget/rate expectation before
  this becomes routine-by-habit even if not routine-by-design.
- **API access model**: the original pull was a manual CSV export, not scripted. Does DataBento's
  API actually support a clean programmatic single-bar/few-bar 1-minute pull suitable for calling
  from `quant-reconcile`, or does the raw multi-venue-record-per-minute shape (mentioned in
  `tasks/finalize_targeted_promotion.md` — "combined by hand via min-of-lows/max-of-highs")
  need its own normalization step built first?
- **Where does DataBento's value live? Partially resolved by `011_add_market_data_archive`**
  (croicu/quant-data#35, closed) — `dim_provider.role` gained a third value, `'advisor'`
  ("can suggest a value but has no autonomous authoring rights"), and `'databento'` was seeded as
  a `dim_provider` row with that role. But that migration deliberately gave it **zero footprint
  beyond the identity row itself** — no archive entry, no `fact_reconciliation_participant` row,
  since neither of the two planned finalize APIs (`tasks/finalize_targeted_promotion.md`) ever
  names `databento` as the thing being accepted. So the `dim_provider` row exists now, but the
  original question — does a DataBento pull actually get persisted anywhere (matching this file's
  "no persistence at all" option), or does this task still want its own storage (the narrower
  `stuck_bar_verification` table option)? — is still open. Revisit once
  `finalize_targeted_promotion.md`'s two-API design is actually implemented, since that's what
  determines whether "fetch-and-display only" is sufficient or whether DataBento verification
  needs to leave its own audit trail.
- **Field-group grain**: `fact_pending_manual_resolution` operates at (bar, field_group) grain
  (today just `'ohlc'`). Does a DataBento pull fetch/compare the whole bar or just the disputed
  field_group?
- **Credentials/config**: where do DataBento API credentials live — `settings.json`/
  `settings.local.json`'s `postgres`/`ibkr` sections already show the pattern (secrets in the
  gitignored local file), so likely a new `settings.databento` block, but not decided.
- **Flag shape**: a `--finalize`-mode flag (bundling with `tasks/finalize_targeted_promotion.md`'s
  own targeted-promotion CLI work, since both touch the same pending-bar workflow) vs. a standalone
  `quant-reconcile --verify-databento` flag independent of targeted promotion — not decided. These
  two task files likely need to converge on one combined CLI design rather than shipping
  overlapping flags.

## Implementation plan

<!-- Not started -- pending the open questions above. -->

## Test results

<!-- Not started. -->
