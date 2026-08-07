# Yahoo Data Sanitization

## Status: Done — mechanism implemented, calibrated against real data, and live-verified
(2026-08-07). See "Recalibration and session-boundary fix (2026-08-07)" below for the final
summary. Tracked in croicu/quant-data#32 (opened by the repo owner — left open per `CLAUDE.md`'s
"Who closes an issue" rule; a summary comment posted there, but closing is the opener's call).

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

- **Resolved (2026-08-06): placement is reconcile time, not ingest.** `_run_outlier_detection_pass`
  runs as the first step of `_run_automatic_pass`, before Tiers 1-4, so a bar rejected this run can
  still auto-promote its candidate in the same invocation. Chosen specifically because it's the
  only way to sweep the *existing* stuck backlog retroactively — ingest-time placement would only
  ever touch new data going forward. (The original "t+1/t+2 doesn't exist yet at ingest time"
  concern turned out not to actually be a blocker either way — `fetch_bars` returns a whole day at
  once, so a single day's batch is self-sufficient for the window — but it didn't change the
  retroactive-sweep conclusion.)
- **Implemented (2026-08-06), croicu/quant-data#32**: `reconcile/outlier_detection.py` (pure,
  unit-tested, no DB access — `is_bar_rejected`), `migrations/010_add_data_quality_thresholds.sql`
  (per-(provider, ticker) coefficient overrides, no rows seeded, falls back to the module's
  `DEFAULT_K_*` constants), new `PostgresDatabase.fetch_data_quality_thresholds`/
  `fetch_whistleblower_accepted_staging_rows`/`mark_staging_bars_rejected` (batched, one commit).
  209/209 tests pass, `ruff` clean. See `docs/ARCHITECTURE.md`'s `reconcile` section for the exact
  window-building and session-transition-cut mechanics.

## Recalibration and session-boundary fix (2026-08-07)

The seed coefficients (3/6/4/8) were never validated before this session — the first real run
against production data rejected **10,126 of 23,938 whistleblower bars (42.3%)**, confirmed by
spot-check (SPY 2026-07-23 13:31-13:45) to be ordinary, internally-consistent price ticks, not
outliers. Root cause: the `± 2`-minute MAD window was self-contaminating — a genuine target spike
inflated its own reference scale, and two coincidentally similar background diffs elsewhere could
collapse the scale to near-zero, producing wild ratio swings unrelated to actual plausibility.

**Fix 1 — wider window, target excluded from its own reference.** Widened the background sample to
`± BACKGROUND_HALF_WINDOW_MINUTES` (20 minutes) and excluded the two diffs touching the target
itself from the MAD calculation, using only real *background* consecutive-pair diffs. Re-tuned
globally on real data: `k_reversal_oc=300, k_trend_oc=600, k_reversal_hl=400, k_trend_hl=800`. This
alone fixed a real false positive caught by eye (SPY 2026-07-31 10:47 ET, an ordinary uptrend
candle that scored 87 under the old window purely from a coincidental near-zero MAD — the same
bar scores ~2.2-2.9 under the new one) *and* resolved what had looked like `DOG`'s "genuine 20%+
noise floor" under the old per-ticker-override approach — the inflated ratios there were largely
the same self-contamination artifact, not real signal. No per-ticker exemption needed once fixed
properly; the `data_quality_thresholds` override table stays empty.

**Fix 2 — session-boundary blind spot.** The literal last/first bar of a session segment (9:30
open, 16:00 close) was structurally unevaluable: `is_bar_rejected` required *both* immediate
neighbors present, but a segment's true edge bar has no legitimate same-segment neighbor on one
side by construction. Confirmed live: SPY 2026-07-29 16:00:00 ET (`high` frozen at 740.4873 while
the real price fell to ~727) sat `accepted` through multiple runs specifically because 16:01 ET
either belonged to a different segment or was `incomplete`. Fixed with two changes: (1) each
segment's last/first `BACKGROUND_HALF_WINDOW_MINUTES` reuse one shared, frozen reference window
(anchored at the last position a full window still fits) instead of shrinking per-bar as the edge
approaches; (2) a bar with only one usable immediate neighbor is now evaluated one-sided against a
dedicated `k_boundary_oc`/`k_boundary_hl` threshold instead of being skipped. Real-data calibration
of the boundary thresholds (the only bars this path ever applies to) put the confirmed SPY 16:00
case at the p99 ratio (~40 for `hl`), with ordinary boundary bars at p97.5 (~16) or below —
`k_boundary_oc=20, k_boundary_hl=20` (`k_boundary_hl` lowered from an initial 25 after a *second*
confirmed case, SPY 2026-07-28 16:00 ET, scored ~21 — just under 25, independently verified against
DataBento well before this detector existed).

**Final live result** (full reconstructed staging set, 108,918 whistleblower rows evaluated): 188
rejected (0.79% of the accepted+rejected pool). All 3 of the original DataBento-confirmed SPY cases
(07-28, 07-29, 07-30, all 16:00 ET) now correctly caught, plus one new unconfirmed SPY candidate
(08-05 16:00 ET — flagged, not yet visually verified) and 185 across `DOG`/`RWM`/`SH`, sampled and
confirmed to be genuine frozen-tick patterns (`open=high=low=close` held across the window). Zero
false positives found in spot-checks after the fix; `SPY`/`QQQ`/`DIA`/`IWM` sit at 0-0.10%.

Explicitly **not** covered by this mechanism: the two documented cross-provider tolerance-failure
cases from `tasks/finalize_targeted_promotion.md` (SPY 2026-07-27 09:50/09:51 ET, SPY 2026-07-28
09:30 ET) — both still sit unresolved in `fact_pending_manual_resolution`. This detector only
catches shape anomalies *within* `yfinance`'s own series; a value that's wrong only *relative to*
`ibkr` (nothing implausible about it in isolation) is a different failure mode, handled by Tier 2's
existing per-field learned tolerance and (eventually) `finalize_targeted_promotion`'s targeted
`--finalize`, not by this check.

## Open questions

- **Quantify the actual impact on the 622-bar backlog.** Not yet checked how much of the original
  stuck-bar backlog (`DOG` especially) this pattern explains vs. genuine multi-field disagreement.
- **The 2026-08-05 SPY 16:00 ET candidate** flagged by the recalibrated boundary check hasn't been
  visually confirmed the way the three original DataBento cases were — worth a candlestick check
  before treating it as validated.

## Implementation plan

Done — mechanism, recalibration, and session-boundary fix all implemented and live-verified. No
further mechanism work planned; only the open questions above remain.

## Test results

Full unit suite passes (`tests/unit/test_outlier_detection.py`'s pure-algorithm cases — reversal
vs. trend, one-sided boundary checks, missing-neighbor handling, per-field independence, custom
thresholds — plus `test_reconcile_cli.py`'s end-to-end orchestration cases), `ruff format`/
`ruff check` clean. Live-verified against the real database end-to-end, including a full
staging-only reconstruction (`fact_market_data_1min` restored to staging and re-cleared to
simulate a from-scratch reconcile run) to get a clean, complete before/after comparison across the
whole recalibration.
