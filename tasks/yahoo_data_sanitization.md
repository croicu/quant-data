# Yahoo Data Sanitization

## Status: Brainstorm

## Problem statement

Investigating the 3 pending `SPY` bars (2026-08-03/04, see `tasks/finalize_targeted_promotion.md`'s
Problem statement for the full writeup) turned up more than just "these 3 bars disagree." Comparing
raw 3-day `staging_market_data_1min` candlestick charts for `ibkr` vs. `yfinance` around the same
window (`tasks/IBKR - 2026.7.28-30.png` / `tasks/Yahoo - 2026.7.28-30.png`) showed:

- `ibkr`'s curve is smooth and continuous across all three days — no isolated wicks anywhere.
- `yfinance`'s curve traces the same overall shape but has sporadic single/few-minute spikes with
  nothing in `ibkr` at the same instant: two on 07-28 right before the pending bar, a cluster around
  16:00 on 07-29 (matching the bad `H`), and on 07-30 there are *more* of the same spikes later in
  the afternoon (~16:30, ~17:00, ~17:10) that never became pending bars at all.
- The 3 known pending bars are exactly this pattern: one implausible extreme field (`L` on 07-28/
  07-30, `H` on 07-29) while O/C stay close between providers. Exact values in
  `tasks/finalize_targeted_promotion.md`.

**Why the other 07-30 spikes never surfaced as pending**: reconciliation only flags a disagreement
when both providers actually reported a value for that minute to compare. Those extra spikes are
presumably minutes where `ibkr` simply has no bar at all (a coverage gap, not agreement) — so
nothing gets compared, and they're invisible to the pending queue even though the same Yahoo noise
is clearly present. Reassuring half: since `yfinance` is whistleblower-only (never auto-promotes,
`tasks/quant_reconcile.md`'s design), a bad Yahoo tick with no `ibkr` counterpart just leaves that
minute *missing* from `fact_market_data_1min`, not corrupted with a bad value.

**Why it matters**: every one of these spikes that *does* have an `ibkr` counterpart becomes a false
"stuck" bar requiring manual review, even though `ibkr`'s own value was fine and would resolve
cleanly via Tier 2 (agreement) if not for `yfinance`'s single bad field dragging the whole
`field_group` comparison outside tolerance (`_max_field_diff` takes the max across all 4 OHLC
fields — one bad field poisons the whole group's comparison). This is very likely contributing to
backlog noise beyond just these 3 `SPY` bars (not yet quantified against the full 622-bar backlog).

**Second independent occurrence (2026-08-05), `SPY` 2026-07-27 09:50/09:51 ET** — one of the
`tasks/finalize_targeted_promotion.md` "New pending-bar review candidates" bullets
(`close`@09:50 `ibkr` 743.95 vs. `yfinance` 743.919982910156; `open`@09:51 `ibkr` 743.91 vs.
`yfinance` 743.950012207031). Initially looked from a candlestick plot (`tasks/Conflict -
2026-07-27.png`) like it might be a timestamp/off-by-one bug — `yfinance`'s bar shape appeared
shifted one minute from `ibkr`'s. Verified directly against `staging_market_data_1min`: both
providers' `date_id`/`time_id`/`timestamp` are internally consistent for both minutes, so it's
not a keying bug. Two things combined to create the visual illusion: (1) `ibkr`'s 09:50 `close`
(743.95) is nearly identical to `yfinance`'s 09:51 `open` (743.950012...), an ordinary
price-continuity coincidence across the minute boundary; (2) `purge_staging_bar` only ever
purges candidate (`ibkr`) rows, never the whistleblower's, so in the surrounding window `ibkr`
had staging rows *only* at the two still-pending minutes while `yfinance` had one every minute —
a sparse-vs-dense series mismatch that likely misaligned whatever plotting script built that PNG
(outside this repo). Net effect: this specific bar is **not** a sanitization case (no single
implausible extreme field the way the original 3-bar pattern showed) — flagged here only because
it's the same investigative thread, and because ruling out "timestamp bug" was itself worth
recording before this was mistaken for evidence of an ingest-time defect. The DataBento-verified
outlier pattern from the original 3-bar case remains the actual motivating evidence for this task.

## Design decisions

- **Disposition: mark the outlier bar `incomplete=True` and keep the row, don't discard at
  ingest.** Considered discarding outlier rows outright before they ever reach staging, but decided
  against it: this investigation's own findings came directly from the raw `staging_market_data_1min`
  values, so silently dropping them at ingest would trade away exactly that audit trail for the next
  time this needs debugging. Reuses the existing `incomplete` flag and Tier 1 (`_resolve_completeness`
  in `reconcile/algorithm.py`), which already auto-promotes a candidate when the whistleblower's bar
  is `incomplete` — so a detected bad tick resolves automatically via existing machinery, with zero
  changes needed to comparison/promotion logic or the atomic-field-group model.
- **Re-opened for discussion (2026-08-05)**: framed with the DV team as "remove the outliers,"
  which reads as discarding the bad value outright — in tension with the disposition above (keep
  the row, just flag it). Worth explicitly reconciling before implementation: "remove" could mean
  (a) literally drop the row at ingest (the option already considered and rejected, for the audit-
  trail reason above), (b) null out just the offending field while keeping the row/other fields, or
  (c) plain shorthand for "stop it from blocking reconciliation," i.e. the existing
  `incomplete=True` disposition already satisfies the actual intent. Carry this ambiguity into the
  GitHub issue rather than assuming which one was meant.
- **Direct dependency on `ingestion_coverage` discovered (2026-08-06), depends on how the "remove
  vs. keep" tension above resolves**: "mark incomplete, keep the row" has no dependency — the row
  still exists in staging, so it flows through the *existing* Tier 1 completeness path
  (`_resolve_completeness`), which already auto-promotes on an incomplete whistleblower value.
  "Remove the row entirely" is different: a deleted yfinance row makes that minute
  whistleblower-*absent*, indistinguishable from "not yet ingested" without `ingestion_coverage`
  confirming the date range really was covered (see `tasks/`'s pipeline-accuracy-hardening
  context — `ingestion_coverage`'s write path is still unfinished; `quant-ingest` never writes to
  it, only migration 008's one-time backfill ever populated it). Without that, every removed
  outlier just becomes a *new* orphaned/unaccounted bar (confirmed live: 12,061 `ibkr` rows
  currently stuck this exact way from ordinary `yfinance` coverage gaps, ~20% of all
  `staging_market_data_1min`) — relocating the problem, not fixing it. So picking "remove"
  implicitly commits to finishing `ingestion_coverage`'s write path first or alongside; "mark
  incomplete" has no such prerequisite and could ship independently, sooner. Surface this
  explicitly before the DV team picks a direction — it changes the actual tradeoff, not just the
  wording.

## Open questions

- **What actually defines "outlier"?** The crux of "sanitize," still open. Candidates:
  - Fixed relative threshold: `H`/`L` more than some % away from the bar's own `O`/`C` range — simple,
    but the threshold is a magic number needing justification.
  - Reuse `provider_pair_disagreement`'s existing per-provider/field-group `stddev` as the scale,
    even though that stat measures *cross-provider* disagreement, not *within-bar* plausibility — a
    related but different quantity; may or may not transfer.
  - Compare against the same 3-bar window Tier 3 (`boundary_fix`) already fetches — is this minute's
    `H`/`L` wildly outside what `t-1`/`t+1` imply? Reuses data already being pulled, most consistent
    with how this codebase already handles "is this bar's value plausible given its neighbors."
- **Where does the check live?** Ingest time (`providers/yfinance.py`, marking `incomplete=True`
  before the row is written to staging) vs. a dedicated step in `reconcile`'s automatic pass (can
  also retroactively mark rows already sitting in staging from before this exists, which ingest-time
  alone can't reach).
- **`yfinance`-specific or general?** `dim_provider` is explicitly not hardcoded to two rows
  (`docs/SCHEMA.md`) — should this be a generic per-provider staging-quality gate (any provider's
  bar can be flagged, not just the whistleblower's), or is it fine to scope this narrowly to
  `yfinance` for now since it's the only provider showing this pattern today?
- **Quantify the actual impact first?** Not yet checked how much of the 622-bar backlog (`DOG`
  especially, 499 stuck) this same single-bad-field pattern explains vs. genuine multi-field
  disagreement — would sharpen whether this is a small cleanup or a significant chunk of the
  backlog's real cause.

## Implementation plan

<!-- Not started -- pending the open questions above. -->

## Test results

<!-- Not started. -->
