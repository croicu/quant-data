# IBKR/Massive MAD Calibration -- Findings

Assembled by `.exp/_shared/report.py` from `manifest.json` (git sha `1f80fd0aa3925e2ba6c071eb1897916c7aa7d25e`).
Range: `2025-12-31`..`2026-08-21`. Tickers measured: SPY (SPY only -- see caveats).

## Open questions and escalations (read this first)

1. **Real production data-quality bug found mid-task, not yet fixed there**: `yfinance`'s stored OHLC
   values carry float32 rounding artifacts (e.g. `737.239990234375` instead of `737.24`) -- a storage-
   precision quirk in the ingest/staging path. Worked around locally in E6/E8 (round to cent precision
   before comparing); the underlying production path still has it. **Needs its own follow-up issue.**
2. **Every number in this document is SPY-only.** `dim_ticker` now has 8 tickers, but only SPY has a frozen
   unpurged staging window -- none of these recommendations (k=3.0, k_volume, the stationarity/coverage
   reads) have been verified on any other ticker.
3. **E3's raw MAD is exactly 0.0 (degenerate)** -- any production adoption of a MAD-based tolerance MUST
   pair it with a nonzero floor (`materiality_floor` already exists for this) or it will reject every
   nonzero disagreement outright. Not optional, not solved here.
4. **Two real anomalies remain unexplained**: E1's RTH-vs-pre/post agreement gap (volume hypothesis
   tested and rejected, R^2~0) and E5's March 2026 flag-rate outlier (not a split/adjustment per E2).
   Neither blocks the recommendation below, both are worth a closer look if revisited.
5. **Structural limitation, true regardless of any experiment's result**: a two-provider band cannot
   detect correlated error (both providers agreeing while both are wrong). This method does not retire
   `yfinance` -- see "Role of yfinance after this task" in the task file.

---

## 1. E0-E2: corrections applied, and whether any invalidate the approach

- **E0 (join integrity)**: RTH `both` coverage 99.379% (gate >= 95%, PASSED), 161 trading days. No correction needed.
- **E1 (alignment)**: winning lag = `0` across every segment/regime. No correction applied to `load.py`.
- **E2 (adjustment)**: outcome = `absent` (0 of 160 days deviating). No correction applied.

**None of E0-E2 invalidate the approach.** All three came back clean on SPY -- coverage sufficient,
alignment already correct at lag 0, no split/adjustment mismatch. The method proceeds on an unmodified join.

---

## 2. E3: MAD vs Welford verdict

- sigma collapses **-92.4282%** when just the top 0.1% of `|d|` is trimmed (585,088 pooled observations).
- Raw MAD is exactly 0.0 (both pooled and per-field) -- the ratio is mathematically undefined, a more extreme result than a finite ratio would be.
- **Implied window state cost if adopted: 585,088 pooled observations** (no O(1) streaming update, unlike Welford).

**Verdict: data SUPPORTS the MAD switch**, with the degenerate-MAD caveat in the open questions above.

---

## 3. Recommended k and g

- **Recommended k = 3.0** (OHLC price band) -- matches production's existing `DEFAULT_RECONCILE_K`.
- **E6 quality at k=3.0**: precision and recall proxies computed over the 7,420-bar overlap window (`2026-07-27`..`2026-08-21`), `n_whistleblower_flagged=616`. See `e6_validation.parquet` for the full k sweep.
- **E4 spend at k=3, all g**: the whole (k, g) grid costs **$0.0002-$0.27/ticker** for the OHLCV-1m schema -- spend does not discriminate between k choices at all; the pick above is driven entirely by E6 quality.
- **Volume band (E7, separate multiplier, not comparable 1:1 to the OHLC k)**: recommend k_volume =~ 18.784 if a volume band is ever built (hypothetical -- see E7's own caveat; not implemented).

---

## 4. E5: stationarity verdict

- Band calibrated on `2026-08`, `k` fixed at 3.0, applied unchanged to each earlier month (8 months, 0 low-sample).
- Flag rate range: 5.60% - 8.39%. Trend: **flat**.

**Verdict: one global band** -- flat, not rising going back. No time-varying band or hard trust cutoff
date needed on this evidence. (See open question #4 above re: the March outlier.)

---

## 5. E7: volume verdict

- Center spread 0.0236 (threshold <= 1.0), dispersion 0.1031 (threshold <= 2.0) -> **STABLE + MODEST (gate passes)**.
- Tail-fatness ratio 5.6209 -- a real heavy tail despite the modest MAD. Exact k for a 1.5% flag rate: **18.784**.

**Verdict: a separate volume band is hypothetically viable** (per the gate), but this is evidence for a
possible future follow-up, not a description of current behavior -- production runs no cross-provider
comparison on volume today (`005_remove_volume_field_group`), and building this stays out of scope here.

---

## 6. E6b: is yfinance independent enough to be a third corner?

- Dispersion test: `massive-yfinance` conditional MAD (9.460e-06) is NOT tighter than `ibkr-massive`'s (4.528e-06) -- no shared upstream by that measure.
- Naive pooled correlation (`ibkr-yfinance` vs `massive-yfinance`) = 0.971 -- **mechanically inevitable wherever `ibkr==massive` exactly, not meaningful, do not use.**
- Disagreement-only correlation (n=5,831, the real test): `im_my`=-0.9274, `im_iy`=-0.006, `iy_my`=0.3797.

**Verdict: yfinance is independent enough** on the task's own literal dispersion test. The disagreement-only
correlation reveals a real, different pattern -- yfinance tracks `ibkr` closely on disagreement bars (not
`massive`), the opposite pairing from what E6b was designed to worry about. **This does not discount the E6
proxies** -- if anything it means yfinance's vote isn't circular with the `ibkr`/`massive` comparison being
tested. Still bounded evidence, not ground truth, per the task's own framing.

---

## 7. E8: which band is primary for the historical period?

- Confusion matrix at k=3.0: both=42, MAD-only=58, whistleblower-only=574, neither=6746.
- Jaccard = 0.0623. MAD misses 93.2% of what the whistleblower flags (superset threshold was <=10%).

**Verdict: SUBSTANTIALLY DISJOINT -- coexistence is earned, not MAD-replaces-yfinance.** The MAD band owns
the pre-overlap historical period (yfinance was never available there anyway); the existing yfinance/Welford
band keeps owning the recent/rolling period it already covers. `materiality_floor_tolerance.md` and
`variance_floor_clamp.md` are **NOT** retirable on this evidence.

**Circularity caveat (task's own explicit instruction)**: E6 and E8 are both computed *using* yfinance.
A disjoint result isn't the direction circularity would bias toward (circularity would inflate an apparent
*superset* finding, not manufacture a disjoint one) -- but this is recorded per the task's instruction
regardless, and the result holds for one window, one alignment regime, one k.

---

## 8. Proposed k recalibration cadence

yfinance's ~30-day rolling window is the only mechanism for recalibrating k going forward (per the task's
"Role of yfinance after this task" section). Proposed cadence, not yet implemented:

- **Monthly**, aligned with E5's own calibration-month pattern: recompute the conditional-MAD band basis
  on the trailing month, re-run E6's precision/recall proxy against that month's fresh overlap, and only
  change the production k if the recommendation shifts by more than one grid step (E4_K_GRID granularity).
- **Drift trigger**: if a monthly recalibration run's flag rate at the current k moves outside the range
  observed in E5 (5.6%-8.4% across 8 months), treat that as a signal to investigate before
  the next scheduled recalibration, not wait for it.

---

## 9. Databento shortlist

Flagged ranges at the recommended k=3.0, gap-merged at g=15 minutes, over the full
`2025-12-31`..`2026-08-21` range (not just the overlap window) -- this is what would actually
need Databento verification for the historical period. Ordered by flag density (flags per billed minute) so
the highest-value ranges come first if credit is limited; per E4, total cost is negligible ($0.0002-$0.27
for the whole grid) so in practice all of these are affordable regardless of ordering.

**SPY**: 1233 total ranges, $0.0099 total cost. Top 20 by flag density:

| range_start | range_end | billed_minutes | n_flags | flag_density | cost_usd |
|---|---|---|---|---|---|
| 2026-07-28 13:27:00 | 2026-07-28 13:30:00 | 4 | 4 | 1.0 | 8e-06 |
| 2026-03-12 13:30:00 | 2026-03-12 13:32:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-03-23 14:02:00 | 2026-03-23 14:04:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-03-31 13:29:00 | 2026-03-31 13:31:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-03-31 19:58:00 | 2026-03-31 20:00:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-05-19 19:59:00 | 2026-05-19 20:01:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-07-17 14:10:00 | 2026-07-17 14:12:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-08-04 19:57:00 | 2026-08-04 19:59:00 | 3 | 3 | 1.0 | 6e-06 |
| 2026-01-02 14:30:00 | 2026-01-02 14:31:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-03 00:04:00 | 2026-01-03 00:05:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-09 14:29:00 | 2026-01-09 14:30:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-12 14:24:00 | 2026-01-12 14:25:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-14 13:59:00 | 2026-01-14 14:00:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-15 14:29:00 | 2026-01-15 14:30:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-21 12:46:00 | 2026-01-21 12:47:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-28 17:27:00 | 2026-01-28 17:28:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-29 12:40:00 | 2026-01-29 12:41:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-01-29 21:34:00 | 2026-01-29 21:35:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-02-04 10:17:00 | 2026-02-04 10:18:00 | 2 | 2 | 1.0 | 4e-06 |
| 2026-02-05 14:29:00 | 2026-02-05 14:30:00 | 2 | 2 | 1.0 | 4e-06 |

---

*Assembled from `results/ibkr_massive_mad/manifest.json` and a small dedicated read for the Databento
shortlist (see this script's own docstring for why that one section needed a live computation).*
