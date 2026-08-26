# IBKR/Massive MAD Calibration -- Findings

Assembled by `.exp/_shared/report.py` from `manifest.json` (git sha `df2a04511298e627512abcde0b3d1e165fd7e908`).
Range: `2025-12-31`..`2026-08-21`. Tickers measured: SPY (SPY only -- see caveats).

## Open questions and escalations (read this first)

1. **Headline finding: the historical (pre-overlap) period is roughly an order of magnitude less protected
   than the whistleblower-covered period, and Databento cannot close that gap.** At the recommended k, the MAD
   band catches only 6.8% of what the yfinance whistleblower catches on the overlap window
   (E8: 93.2% missed) -- this is not a footnote, it is the actual finding this task exists to
   surface. Databento only deepens resolution on bars the band *already* flagged (E4/E9's shortlist); it adds
   nothing to sensitivity, since it is never consulted on a bar the band didn't flag in the first place. This
   is a capability bound on the entire historical backfill, not just an E8 result -- it should shape
   `tasks/retroactive_revision.md`'s scope, not just be noted alongside it.
2. **Real production data-quality bug found mid-task, not yet fixed there**: `yfinance`'s stored OHLC
   values carry float32 rounding artifacts (e.g. `737.239990234375` instead of `737.24`) -- a storage-
   precision quirk in the ingest/staging path. Worked around locally in E6/E8 (round to cent precision
   before comparing); the underlying production path still has it. **Needs its own follow-up issue,**
   including whether accumulated `provider_pair_disagreement` stddev needs recomputation once fixed.
3. **Every number in this document is SPY-only.** `dim_ticker` now has 8 tickers, but only SPY has a frozen
   unpurged staging window -- none of these recommendations (k=3.0, k_volume, the stationarity/coverage
   reads) have been verified on any other ticker.
4. **E3's raw MAD is exactly 0.0 (degenerate)** -- any production adoption of a MAD-based tolerance MUST
   pair it with a nonzero floor (`materiality_floor` already exists for this) or it will reject every
   nonzero disagreement outright. Not optional, not solved here.
5. **Two real anomalies remain unexplained**: E1's RTH-vs-pre/post agreement gap (volume hypothesis
   tested and rejected, R^2~0) and E5's March 2026 flag-rate outlier (not a split/adjustment per E2).
   Neither blocks the recommendation below, both are worth a closer look if revisited.
6. **Structural limitation, true regardless of any experiment's result**: a two-provider band cannot
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

**Correction (2026-08-26 pre-report review)**: E6's precision proxy originally had a real bug -- its `typical`
yfinance-deviation baseline was a plain median over *all* triple-overlap (bar, field) instances, but 82-89% of
those are exact ties (yfinance matches one candidate to the cent), so the median silently collapsed to exactly
0.0 for every field. That degenerate 0.0 changed what "decisive" meant and produced a non-monotonic, unreliable
precision curve (originally reported: 50.4% / 38.2% / 82.7% at k=1/2/3). **Fixed** in `.exp/validation/
overlap_validation.py` by using the conditional (nonzero-only) median instead -- the same convention already used
for conditional MAD elsewhere in this task. Corrected precision curve below; k=3's number happens to be unchanged
(82.7%), but k=1 and k=2 were both substantially wrong and are now much lower.

- **Recommended k = 3.0** (OHLC price band) -- matches production's existing `DEFAULT_RECONCILE_K`.
- **E6 quality at k=3.0**: precision and recall proxies computed over the 7,420-bar overlap window (`2026-07-27`..`2026-08-21`), `n_whistleblower_flagged=616`. See `e6_validation.parquet` for the full k sweep.

  | k | field flags | precision | recall |
  |---|---|---|---|
  | 1.0 | 2016 | 4.6% | 84.7% |
  | 2.0 | 304 | 30.6% | 20.9% |
  | 3.0 | 104 | 82.7% | 6.8% |
  | 4.0 | 49 | 75.5% | 4.2% |
  | 5.0 | 48 | 75.0% | 4.1% |
  | 6.0 | 34 | 73.5% | 2.6% |
  | 8.0 | 27 | 70.4% | 1.8% |
  | 10.0 | 20 | 75.0% | 1.1% |
  | 15.0 | 13 | 76.9% | 0.5% |
  | 20.0 | 13 | 76.9% | 0.5% |

- **E4 spend at k=3, all g**: the whole (k, g) grid costs **$0.0002-$0.27/ticker** for the OHLCV-1m schema -- spend does not discriminate between k choices at all.
- **`k` was NOT actually chosen from an intersection of quality and spend, despite the task's own gate wording**
  -- E4 already showed spend is negligible everywhere in the grid, which removes spend from the intersection
  entirely. The real binding constraint is **human review capacity**: Pass 2 (acting on a MAD flag) is a manual,
  deliberate review step, and nothing in E0-E8 quantifies how many flags a reviewer can actually process. Under
  that framing, and now that the precision bug above is fixed, **k=3.0 is the right pick on the corrected
  numbers, not just the convenient one**: k=1's 84.7% recall comes at only ~4.6% precision (93 genuine hits
  buried in 2,016 field flags -- a reviewer would wade through ~22 flags per real one), while k=3's 6.8% recall
  comes at 82.7% precision (86 of 104 field flags genuine). **State `k=3.0` explicitly as a review-capacity
  choice (favoring signal-to-noise for a human reviewer over completeness), not a spend-driven one** -- if
  review capacity is ever large enough to absorb k=1/k=2's noise, recall could be traded back up, but nothing
  here establishes that capacity exists.

**Units mismatch, and a marginal-discovery framing (2026-08-26 second review pass, R2)**: precision above is
counted per (bar, field) -- e.g. k=3's 104 field flags can exceed its 42 whistleblower-caught *bars* because one bar
can contribute flags on multiple fields. Read the precision percentages only within a fixed k, never compared
across the precision/recall columns as if they shared a denominator. On the corrected numbers, k=1 nets 93 genuine
(bar, field) hits against k=3's 86 -- **only 7 more genuine hits for roughly 19x the review volume (2,016 vs 104
field flags)** -- a cleaner argument for k=3.0 than either percentage alone.

**Is the precision proxy just measuring yfinance-IBKR affinity, not correctness? (R1/B4) -- checked, not
disqualifying at k=3.** E6b found yfinance tracks ibkr closely on disagreement bars (`im_my=-0.927`), raising the
concern that a "decisive" flag might just mean "yfinance agreed with ibkr" rather than genuinely picking the
correct side. Checked directly: among k=3's 86 decisive field flags, yfinance sides with ibkr on 58.1% (50) and
with massive on 41.9% (36) -- a real lean toward ibkr, but far from the near-automatic 90%+ split B4's concern
would predict if the proxy were purely measuring affinity rather than correctness. **The split does widen sharply
at higher k** (59% at k=1-2, 58% at k=3, rising to 84% at k=8 and 93% at k=10) -- so the affinity concern is real
and grows at the tail, but at the recommended k=3.0 specifically it is not the dominant driver of the precision
number. **k=3.0 does not need to be held conditional on further B4 work; the high-k end of the grid should be
read with more caution than the k=3 recommendation itself.**

**Null baseline for precision (R4)**: the unconditional "decisive" rate over all 29,680 (bar, field) instances in
the overlap window (not just MAD-flagged ones) is **0.313%** -- the correct like-for-like chance comparator (same
units as the precision column, unlike the whistleblower's bar-level 8.3% flag rate, which is a different
denominator). Every k in the grid clears this by a wide margin: even k=1's corrected 4.6% is ~15x the null rate,
and k=3's 82.7% is ~264x it. **No operating point tested is anywhere near indistinguishable from chance.**
- **Volume band (E7, separate multiplier, not comparable 1:1 to the OHLC k)**: recommend k_volume =~ 18.784 if a volume band is ever built (hypothetical -- see E7's own caveat; not implemented). This is an empirical
  quantile of the log-ratio distribution (inverted from a 1.5% target flag rate), not a MAD multiple in the same
  sense as the OHLC k -- the 1.4826 scaling that gives MAD its distributional meaning doesn't apply here.

---

## 4. E5: stationarity verdict

- Band calibrated on `2026-08`, `k` fixed at 3.0, applied unchanged to each earlier month (8 months, 0 low-sample).
- Flag rate range: 5.60% - 8.39%. Trend: **flat**.

**Verdict: one global band** -- flat, not rising going back. No time-varying band or hard trust cutoff
date needed on this evidence. (See open question #5 above re: the March outlier.)

**Why this range (5.6-8.4%) reads so much higher than E4's own flag rate at the same k (1.1-1.4%, section 3's
budget figures) -- resolved 2026-08-26**: these two numbers are answering different questions and were never
meant to match. E4 calibrates its conditional MAD once, pooled over the full 8-month range; E5 deliberately
recalibrates on a single month (August) and freezes that. Verified directly against the warehouse: August's own
per-field conditional MAD (3.6-4.8e-6) is roughly 1.4-2.1x smaller than the full-range pooled MAD E4 uses
(4.7-7.7e-6), which alone would explain a tighter band and a higher rate. But the gap is larger than that ratio
predicts, for a second, independent reason: **August 2026 is itself an atypically fat-tailed month relative to
its own scale**, not just a smaller-scale one. Evaluated against its *own* threshold (i.e. self-referentially,
the way a calibration month always is), August flags 5.60% of its own bars -- April through July, evaluated the
same self-referential way, flag only 0.86-1.40% of their own bars. So the August-calibrated fixed threshold is
both tighter in absolute terms AND happens to reflect a month with proportionally more large disagreements,
and both effects push every month's flag rate up together. **This does not undermine the flat verdict** -- flat
is a claim about the fixed threshold not diverging further as you go back in time, which held (Jan, the
earliest month, is neither the highest nor lowest) -- but the specific 5.6-8.4% absolute numbers are an artifact
of calibrating on an atypical month, not a stable property of the underlying ibkr/massive disagreement, which
(checked month-by-month against each month's own threshold) actually varies quite a bit -- 0.86% to 18.1%. A
future recalibration that lands on a more typical month would likely produce a materially lower absolute flag
rate than this run did, even though the flat *trend* finding would probably still hold.

**Which month carries the 18.1% figure, and is it estimator noise or a real event? (2026-08-26 second review pass,
R3) -- it is January, not March**, and it is not noise: checked directly, January's own-threshold rate (18.07%) is
driven almost entirely by `high`/`low` (1,791 and 1,724 of 18,388 bars, ~9.4-9.7% each) while `open`/`close` barely
flag at all (63 and 56 bars, ~0.3%). It is also sustained through the whole month, not front-loaded on the dataset's
first few days (day-by-day range 12.6%-23.1%, first-5-trading-days average 18.8% vs. the remaining 15 days' 17.8%
-- no onboarding-artifact signature). **This looks like a real, sustained monthly regime difference in `high`/`low`
agreement, not conditional-MAD sampling noise** (n=18,388 is not a small sample). March's own outlier (still
unexplained, open question #5) is a separate, smaller anomaly (1.41% own-threshold) and is NOT closed by this --
that question remains open.

**Consequence for the recalibration-cadence follow-up (section 8)**: because January's elevated tail is real and
sustained rather than noise, a purely rolling monthly recalibration is genuinely exposed to landing on a month
like January and inheriting its (real, not spurious) `high`/`low` looseness -- the same mechanism already observed
with August. This is a real tension with the "monthly recalibration" cadence proposed in section 8, not fully
resolved here -- see that section's own note.

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

**Open tension, not resolved here (2026-08-26 second review pass, R3)**: section 4's finding that January's
elevated own-threshold rate is a real, sustained monthly regime difference (not conditional-MAD sampling noise)
means a purely rolling single-month calibration is genuinely exposed to inheriting a month's real idiosyncrasy
(as already observed with both January and August) rather than tracking genuine drift. That pulls toward
calibrating on the pooled full range instead (E4's basis) for stability -- but pooling also can't detect real
drift if the underlying disagreement characteristics genuinely shift over time, which is the entire reason E5
exists. **This is a real design choice between the two E0-E8 already computed, not a bug to fix -- flagged for
the repo owner rather than decided unilaterally before this cadence is actually built.**

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
