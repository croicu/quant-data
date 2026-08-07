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

- **Outlier definition (2026-08-06 design session, resolves the "what defines an outlier" open
  question below)**:
  - **Purely intra-provider, no cross-provider comparison.** An outlier is a datapoint that
    doesn't fit the statistical pattern of its own series' immediate neighbors — never compared
    against `ibkr`. `ibkr` (the `candidate`) is exactly what gets vetted *against* `yfinance` (the
    `whistleblower`) during reconciliation; letting the plausibility check reference `ibkr` would
    make the whistleblower's own signal depend on the thing it's supposed to be independently
    checking. Also makes the check naturally general (nothing `yfinance`-specific about it,
    resolving that open question too — a generic per-provider staging-quality gate, consistent
    with `dim_provider` not being hardcoded to two rows).
  - **Fields checked: `O`, `H`, `L`, `C`. Not `V`** — volume isn't part of the OHLC `field_group`
    that `_max_field_diff`/`boundary_fix` reason about.
  - **Scale: MAD (median absolute deviation)** of bar-to-bar diffs, per ticker, over the local
    window below. Chosen over plain stddev, which is itself inflated by the outliers it's meant to
    catch. Per-ticker because tickers observably differ in stuckness (`DOG`'s 499 vs. others).
  - **Window: `t-2, t-1, t, t+1, t+2`** (5 bars), symmetric across all four fields.
  - **Directional (reversal vs. trend) shape check**: compare `sign(diff(t-1→t))` vs.
    `sign(diff(t→t+1))`. Opposite signs (reversal/spike shape) → tight threshold — the actual
    signature seen in every confirmed case (one implausible extreme field, O/C stay close). Same
    sign (continuing trend) → much looser threshold, since a persisting move is more likely a real
    price event than a bad tick.
  - **Separate coefficients for OC vs. HL** — `H`/`L` are intrabar extremes and inherently
    fatter-tailed than point-in-time `O`/`C`, even under normal conditions; MAD normalizes each
    field's own typical movement but not this structural tail-shape difference. Four constants:
    `k_reversal_OC`, `k_trend_OC`, `k_reversal_HL`, `k_trend_HL`. Seed values (gut priors, **not
    yet validated** — validate against the 622-bar backlog and the already-confirmed
    DataBento-verified cases before trusting them):

    | | Reversal (tight) | Trend (loose) |
    |---|---|---|
    | **OC** | 3 | 6 |
    | **HL** | 4 | 8 |

  - **Thresholds stored in the database, seeded with the above, adjustable per ticker.** A
    separate config surface from `provider_pair_disagreement`'s cross-provider tolerances (that one
    may not need a `ticker_id` key; this one does) — related but distinct, not to be unified
    without a separate call to do so.
  - **Session-boundary handling.** Trading day runs 4:00 AM → 20:00 ET, ingested in full. At every
    session-transition point, the window doesn't cross the boundary in the direction where no
    legitimate data exists:

    | Boundary | Time | Backward-only (last bar before) | Forward-only (first bar after) |
    |---|---|---|---|
    | Day start | 4:00 AM | — (no prior-day data) | 4:01 AM |
    | Regular open | 9:30 AM | 9:29 AM | 9:31 AM |
    | Regular close | 16:00 | 15:59 | 16:01 |
    | Day end | 20:00 | 19:59 | — (no next-day data) |

    Applies to all four fields uniformly. This also resolves "real bell-time spikes might get
    falsely flagged" without a separate time-of-day threshold: the bar right after a transition
    only compares forward (never back across the bell), so a genuine 9:30/16:00 move has no
    wrong-direction neighbor to form a reversal shape against. A separate time-of-day multiplier
    was considered and retired as unnecessary.
  - **Missing-neighbor handling (mid-session gaps, distinct from the boundary case)**: a missing
    `t-2`/`t-1`/`t+1`/`t+2` mid-session is an ordinary coverage gap (the same kind already
    confirmed live — 12,061 orphaned `ibkr` rows) — comparing across a multi-minute hole would
    corrupt the MAD/reversal computation. Rule: **truncate the window at the gap** (missing `t-1`
    = that side becomes uncheckable, same as a boundary bar; missing `t-2` with `t-1` present just
    loses one point of context). If truncation leaves too few points to compute a verdict at all,
    the bar lands in the `incomplete` state below (not `rejected`) — a bar the check couldn't run
    against has no evidence of a problem, so it shouldn't get a harder disposition than a bar
    actually proven bad. Deliberately *not* treated as "remove the row" either — removal would
    reopen the `ingestion_coverage` dependency above.
  - **Resolved (2026-08-06): single tri-state column replaces the boolean `incomplete` flag**,
    values `accepted` / `incomplete` / `rejected` — working name `data_quality` (adjustable), on
    both `staging_market_data_1min` and `fact_market_data_1min`. Modeled as an `Enum` matching a DB
    `CHECK` constraint, same precedent as `ProviderRole`/`TelemetryLevel`, not a plain string.
    - `accepted` — normal case, nothing flagged.
    - `incomplete` — the existing meaning (e.g. a real zero-volume bar) **and** the
      missing-neighbor "couldn't check" case above both land here; both represent "no confidence
      in this value" without positive evidence it's wrong, so they share a state rather than each
      getting their own.
    - `rejected` — a confirmed outlier: the check ran and found the value implausible. Genuinely
      distinct from `incomplete` so a "confirmed bad tick" is never confused with ordinary
      incompleteness.
    - **`rejected` is treated exactly like `incomplete` by Tier 1** (`_resolve_completeness` in
      `reconcile/algorithm.py`) — same auto-promotion behavior, just a different, auditable reason
      recorded. No new promotion-path logic needed, only widening the check from a boolean test to
      a tri-state one.
    - **Implemented as its own task (2026-08-06), croicu/quant-data#32** — split out since it's a
      breaking change to `quant_data`'s public contract (`OHLCV.incomplete: bool` →
      `OHLCV.data_quality: DataQuality`), announced cross-repo via croicu/quant-scratch#16 per
      `CLAUDE.md`'s Cross-Repo Coordination rule. Migration `009_replace_incomplete_with_data_quality`
      backfills existing data (`TRUE`→`incomplete`, `FALSE`→`accepted`) and drops the old column on
      both `staging_market_data_1min`/`fact_market_data_1min`; every read/write site updated
      (`providers/yfinance.py`, `providers/ibkr.py`, `write_staging_bars`/`write_bars`/
      `promote_bar_to_fact`, `_resolve_completeness`, `_is_matched_bar`, all mocks/tests). `ruff`
      clean, 193/193 tests pass. **Migration not yet applied to the real database** — needs the
      repo owner via `psql` as `quant_data`. No code path sets `rejected` yet — this only lays the
      schema foundation the actual outlier-detection check (still below, threshold/placement still
      open) will use.
    - **Follow-up read API, same #32** — `MarketData.fetch_rejected_whistleblower_bars(ticker,
      start_date, end_date)`, new public `RejectedWhistleblowerBar` (`provider`, `bar`). Answers
      "did `yfinance`'s own raw feed have a bad tick here," deliberately separate from
      `fetch_pending_resolution_bars`: a rejected whistleblower value with an *accepted* candidate
      auto-resolves via Tier 1 and never reaches `fact_pending_manual_resolution`, so this is the
      only way to see it — finds it regardless of resolution outcome since whistleblower rows are
      never purged. No new `quant_reader` grant needed (reuses tables already granted for
      `fetch_pending_resolution_bars`). 195/195 tests pass.

## Open questions

- **Exact threshold validation.** The four seed coefficients above (3/6/4/8) are unfit gut values —
  needs to run against the 622-bar backlog and the already-confirmed DataBento-verified cases to
  check false-positive/false-negative rates before treating them as settled.
- **Where does the check live?** Ingest time (`providers/yfinance.py`) vs. a dedicated step in
  `reconcile`'s automatic pass. The `t+1`/`t+2` dependency (needs bars that don't exist yet at
  ingest time for the current minute) points toward the reconcile pass, which also gets
  retroactive-sweep capability over the existing backlog for free — not yet formally decided.
- **Quantify the actual impact first.** Not yet checked how much of the 622-bar backlog (`DOG`
  especially, 499 stuck) this same single-bad-field pattern explains vs. genuine multi-field
  disagreement — would sharpen whether this is a small cleanup or a significant chunk of the
  backlog's real cause, and doubles as the validation dataset for the threshold question above.

## Implementation plan

<!-- Not started -- pending the open questions above. -->

## Test results

<!-- Not started. -->
