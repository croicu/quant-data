# Task: IBKR/Massive MAD Calibration Experiment

**Status:** E0 done (gate passed); E1 done (lag 0, no correction needed); E2 done (absent, as
expected for SPY); E3 done (data supports MAD switch, but raw MAD is degenerate — needs a
materiality floor); E4-E8 not started
**Type:** Experiment (offline analysis, no production code path)
**Depends on:** One year of ingested IBKR + Massive 1-minute bars already on disk
**Blocks:** `tasks/retroactive_revision.md`, Massive backfill scope, Databento integration decision

---

## Problem

yfinance provides only ~1 month of intraday history, so the whistleblower role is
unavailable for the other ~11 months of the period of interest. IBKR and Massive
cover the full period.

Proposed replacement for the pre-overlap period: flag bars for manual resolution
using a **MAD-based band on the IBKR−Massive difference series**, then resolve
flagged bars by pulling Databento (paid, limited credit) as a sparse oracle.

Before committing to that, several assumptions need to be tested against data we
already hold. This task is the test, not the implementation.

## Structural limitation (true even if every experiment succeeds)

A band on `|ibkr − massive|` cannot detect **correlated error**: both providers
agreeing while both are wrong. `|d|` is small, no flag fires, the bar promotes. This
is the two-provider identifiability problem again — a third source is the only
detector, and Databento cannot serve as one because it is invoked *only on bars the
band already flagged*.

Consequence: this method **does not retire yfinance**. See "Role of yfinance after
this task" below. Nothing in E0–E8 can change this conclusion, because no experiment
here has visibility into the failure mode.

## Non-goals

- Do **not** implement the production tolerance path. This task produces evidence
  and a recommendation; the production change is a follow-up task.
- Do **not** integrate Databento. Output is a *shortlist of ranges to pull*, not a
  client.
- Do **not** touch `fact_market_data_1min` or `staging_market_data_1min`. See
  invariants.
- Do **not** port the yfinance intra-provider MAD sanitizer. That operates on a
  5-bar window over a price *path* (hence the reversal/trend directional split).
  This operates on a *disagreement distribution* per (ticker, field group) over a
  long window. Same acronym, different object. **No directional split here.**

## Invariants

1. **Read-only against the warehouse.** No INSERT/UPDATE/DELETE on any `fact_*`,
   `staging_*`, or `dim_*` table. Add a guard: connect with a read-only role if one
   exists, otherwise assert no write statements in the codepath.
2. **No new provider fetches.** Everything runs on data already ingested.
3. **Reuse existing constants.** Session boundaries (4:00 / 9:30 / 16:00 / 20:00 ET)
   already exist in `src/reconcile`. Import them; do not redefine.
4. **Deterministic.** Any sampling seeded and recorded in the output manifest.
5. **One-way imports.** `experiments/` may import from `src/`. `src/` must **never**
   import from `experiments/`. Enforce with a lint rule or a test — this is the real
   contamination boundary, and it is what makes the experiment deletable later.
6. **Method is pinned.** IBKR bars are keyed by `(provider, method, ticker, period)`.
   Pin the `method` used (expected `TRADES`) and record it in the manifest. Do not
   silently union across methods.

## Layout

Runs in **quant-data**, not quant-scratch: E3 compares against the Welford
estimator, E6 needs the whistleblower `ACCEPTED` validity gate, and E8 needs the
existing yfinance flag set. Those are reconciliation internals, and widening
quant-data's public API to reach them from quant-scratch would be a worse outcome
than co-locating a one-off.

Placed at **repo root, not under `src/`**, so it is not importable as library code
and does not accumulate as pipeline surface.

**Revised 2026-08-25**: one folder per experiment under `.exp/` (renamed twice this session:
`.experiment/` → `exp/` → `.exp/`, each on explicit request — settled on the dot-prefixed short
form, combining `.experiment/`'s structural non-importability as a Python package — `.exp` isn't a
legal package name, so nothing under `src/` can import it even by accident — with `exp/`'s shorter
name), each holding its own single script named for what it does (`coverage.py`, not
`e0_coverage.py`) rather than one shared `experiments/ibkr_massive_mad/` package with an
`e<N>_*.py` file per experiment. Shared code (`config.py`, `load.py`, `manifest.py`; `report.py`
once it exists) lives in `.exp/_shared/`, imported by each experiment script via a `sys.path`
insert of `.exp/` at the top of the script — `_shared` itself is an ordinary (non-dot) package
name, so this is a normal absolute import once that directory is on `sys.path`.

```
.exp/
  _shared/
    __init__.py
    config.py          # tickers, date range, k/g sweep grids, method pin
    load.py            # warehouse -> staging rows (read-only, quant_reader)
    manifest.py        # results/ibkr_massive_mad/manifest.json read/update
    report.py          # assembles findings.md from results/ (not yet built)
  join_integrity/
    coverage.py        # E0
  alignment/            # E1 (not yet built)
  ...                   # one folder per remaining experiment, following E0's pattern
results/ibkr_massive_mad/
  manifest.json      # run params, source table, method, row counts, git sha
  <experiment>/*.parquet + *.png
  findings.md
```

Each experiment is independently runnable and writes its outputs before the next
runs. Later experiments read earlier outputs from `results/`, not by recomputing.

## Preliminary: source and shape

Determine whether the year of IBKR/Massive bars lives in `staging_market_data_1min`,
`fact_market_data_1min`, or both, and record the choice in the manifest with a
one-line justification. Prefer staging — the experiment is about what *should*
promote, so reading promoted rows risks circularity.

Build the difference series in `load.py`:

- Inner join IBKR and Massive on `(ticker, date, time)` after E1's alignment
  correction is known. Before E1 runs, use lag 0.
- Field groups from `dim_field_group`. O/H/L/C in scope for the band; volume is
  handled separately in E7.
- `reference_value` = **midpoint of the two providers**, not the preferred
  provider's value. The metric must be symmetric in its two inputs.
- Relative difference: `d = (ibkr_value - massive_value) / reference_value`.
  Store fractional, consistent with existing tolerance storage.

---

## E0 — Join integrity

**Why:** The difference series only exists on the intersection. Non-random
missingness biases every downstream statistic and is a *separate* manual-resolution
class from disagreement — it must not consume the flag budget.

**Method:** Per (ticker, minute), classify into `both` / `ibkr_only` /
`massive_only` / `neither`. Aggregate by session segment (pre / RTH / post) and by
month.

**Output:** `e0_coverage.parquet`, coverage matrix rendered in findings.

**Gate:** If `both` coverage in RTH is below ~95% for any ticker, stop and
investigate before proceeding — the rest of the experiment is measuring a biased
subsample.

**Status: done, gate passed.** Run: `.exp/join_integrity/coverage.py`. Ticker: SPY (the
only one with a wide-enough unpurged staging window — see `config.py`). Range:
2025-12-31–2026-07-31 (146 trading days, determined from the data itself via RTH presence, not an
external holiday calendar). RTH `both` coverage: **99.315%** — gate passed. Pre/post `both`
coverage: 92.6% / 90.9%, with the rest `ibkr_only` (7.4% / 8.7%) and `neither` only in post
(0.4%). **`massive_only` never occurs anywhere in the whole 7-month grid** (0 of 140,306 expected
minutes) — every minute `massive` reports, `ibkr` also reports; worth carrying into E1/E8 as a
prior (ibkr looks like a strict superset of massive's minute coverage on this ticker). Outputs:
`results/ibkr_massive_mad/join_integrity/coverage_by_minute.parquet` (140,306 rows, one per
expected (day, minute)), `coverage_by_segment_month.parquet` (per-month breakdown), and
`results/ibkr_massive_mad/manifest.json`. One real data issue found and corrected during this run:
a handful of stray SPY rows dated 2026-08-03/2026-08-10 (1-2 rows per provider each) turned out to
be ordinary same-day pipeline activity outside the frozen backfill window, not part of the
dataset — excluded by setting `config.END_DATE = 2026-07-31` (not `2026-08-10`, which the earliest
live check had suggested before this was caught).

---

## E1 — Timestamp alignment

**Why:** If one provider labels bars by open and the other by close, or extended-hours
inclusion differs, every bar disagrees by one bar of movement and MAD silently
absorbs it as baseline noise.

**Method:** Exact-match rate on `high` and `low` — extremal, discrete tick values,
so the test is sharp — between `ibkr(t)` and `massive(t + lag)` for
`lag ∈ {−1, 0, +1}`. Correct alignment produces a **step-function spike**, not a
gentle optimum. Run separately:
- per session segment,
- per DST regime (a fixed-offset bug appears as a 1-hour shift in half the year —
  a full year is exactly what makes this visible).

**Output:** `e1_alignment.parquet`, match-rate-by-lag table per segment/regime.

**Gate:** If the winning lag is nonzero or differs across segments/regimes, the
correction becomes part of `load.py` and **all later experiments rerun on the
corrected join**. If no lag shows a clear spike, escalate — that pattern suggests
a deeper mismatch than an offset.

**Status: done, no correction needed.** Run: `.exp/alignment/match_rate.py`. Ticker: SPY, same
range as E0. Lag 0 wins with a clear step-function spike (75-89 point margin over the runner-up)
in **every** (segment, DST regime) combination — no alignment correction needed in `load.py`.
**Notable finding to carry forward**: even at the correct lag, the exact-match rate on `high`/`low`
tops out well under 100% — RTH lowest (81.6% EST / 87.5% EDT), pre-market highest (93.3% EDT /
97.1% EST), post-market in between (~87.6-87.8%). This isn't an alignment artifact (the spike is
unambiguous) — it's genuine baseline `ibkr`/`massive` disagreement even when bars are correctly
paired, and RTH being the *lowest*-agreement segment (not highest, despite being the most liquid)
is worth carrying into E3/E6 rather than assuming pre/post is where the real noise concentrates.

**Ad-hoc addendum, same run**: tested (and rejected) the hypothesis that `ibkr` volume explains
this — a natural first guess given `materiality_floor`'s own established `ibkr`-volume-vs-
disagreement correlation on the unrelated `ibkr`/`yfinance` pair (issue #40, R²=0.32). Pooling all
segments together, matched bars do show much lower median volume than mismatched bars (~9,600 vs
~39,000) — but that's a **segment confound**, not a real effect: restricted to RTH alone, matched
and mismatched median volume are essentially identical (~86,730 vs ~84,932), and a log-log
regression of volume against disagreement magnitude among mismatched RTH bars gives **R² ≈
0.001–0.003** — no relationship. RTH simply has both higher volume *and* higher disagreement than
pre/post as two separate facts, not one driving the other. The RTH-vs-pre/post agreement gap
remains unexplained by volume; if revisited, trade count/tick density is a more promising next
angle than raw volume. Output: `results/ibkr_massive_mad/alignment/volume_correlation.parquet`.

---

## E2 — Adjustment mismatch

**Why:** A split or differing dividend adjustment puts a step change in the
difference series. Over a long MAD window that either flags an entire segment or
widens the band enough to hide real errors.

**Method:** Per ticker, daily median of `ibkr.close / massive.close`. Look for:
- jumps to a near-rational value (2.0, 0.5, 1.5, 3.0) → split handling mismatch;
- small persistent step offsets → dividend adjustment difference.

No external corporate-actions calendar is needed to *detect* this — only to explain
it. Record the IBKR `method` (`TRADES` vs `ADJUSTED_LAST`) prominently; it changes
the expected answer.

**Output:** `e2_adjustment.parquet`, per-ticker ratio series plot, list of detected
step dates.

**Gate:** Three outcomes, each with a different consequence:
1. **Absent** — proceed unchanged.
2. **Present at identifiable action dates** — fixable with an adjustment dimension;
   record as a follow-up task, apply a correction in `load.py` for the experiment.
3. **Present at unidentifiable dates** — vendor archive reconstruction difference.
   This is the bad case: it caps how far back the band can be trusted and feeds
   directly into E5. Do not paper over it.

**Status: done — ABSENT, as expected for SPY.** Run: `.exp/adjustment/close_ratio.py`. Ticker:
SPY, same range as E0/E1 (145 trading days). Daily median `ibkr.close/massive.close` ratio is
**exactly 1.0 on every single day** — zero deviating days at the 0.5% materiality threshold, no
jumps, no persistent offset. Confirms the repo owner's expectation going in: SPY is an ETF with no
splits/adjustment mismatches expected over a 7-month window, and this only became worth an actual
check (rather than assuming it) because it *will* matter once individual symbols get traded —
those do split, and E2's method/output (the ratio-series plot, the rational-multiple detector)
stays available unchanged for that case. `ibkr` method is `TRADES` (raw/unadjusted) throughout,
as pinned since E0. Output: `results/ibkr_massive_mad/adjustment/close_ratio_by_day.parquet`,
`spy_ratio_series.png` (flat line at 1.0, no jumps visible).

---

## E3 — Does MAD actually beat Welford here?

**Why:** The MAD switch is motivated by Welford's contamination by the outliers it
is meant to catch. That argument should be quantified on our own data, not assumed.
If it fails, `variance_floor_clamp.md` was already sufficient and this whole
direction is unmotivated.

**Method:** On the corrected difference series, per (ticker, field group):
- compute `σ` and `1.4826 × MAD`;
- report the ratio `σ / (1.4826 × MAD)` as a tail-fatness measure;
- recompute both with the top 0.1% of `|d|` removed.

Expectation if the MAD argument holds: `σ` moves substantially, MAD barely moves.

**Output:** `e3_dispersion.parquet` with both estimators, both trimmed and untrimmed,
plus the ratio.

**Gate:** If `σ` is stable under trimming and the ratio is near 1.0, **recommend
against** the MAD switch and close the experiment with that finding. Everything
after this point assumes the ratio is materially above 1.

**Note on state cost:** MAD has no O(1) streaming update. If it wins, the production
follow-up needs either a retained window or a streaming quantile approximation.
Record the window size implied by the winning configuration — it is a real
operational cost and belongs in the recommendation.

**Status: done — data SUPPORTS the MAD switch, with a real caveat.** Run:
`.exp/dispersion/sigma_vs_mad.py`. Ticker: SPY, pooled `ohlc` field group (all four fields
together, reusing `reconcile.algorithm.fields_for_group` per the task's own method — one row per
field is also written as a diagnostic breakdown, not the requested grouping). σ collapses by
**-92.67%** when just the top 0.1% of `|d|` is trimmed (532,584 pooled observations →
532,051) — Welford's own σ is dominated by a small contaminating tail, exactly the argument this
task exists to quantify. Per-field σ-collapse ranges from -63.8% (`close`, most stable) to -95.8%
(`low`, least stable) — consistent with E1's finding that RTH/low-field disagreement runs highest.

**Real wrinkle not anticipated by the task's method: raw MAD is exactly 0.0**, both pooled and
per-field, before *and* after trimming. Not a bug — E1 already established match rates well above
50% on every field, so both the pooled sample's median and its MAD collapse to an exact zero. This
makes the `σ / (1.4826 × MAD)` ratio **mathematically undefined** (division by zero) rather than
merely "close to 1" or "far above 1" — a more extreme outcome than the task's gate anticipated,
and the σ-collapse-under-trimming evidence had to substitute for the ratio as the operative signal
for this gate. **Consequence for any production follow-up**: a raw `1.4826 × MAD` tolerance would
be exactly `0`, rejecting *any* nonzero disagreement — unusable without pairing it with a nonzero
floor. `materiality_floor` already exists in production for exactly this shape of problem
(croicu/quant-data#40), so this isn't a new mechanism to build, but it must be explicitly part of
the production-tolerance follow-up task, not assumed away.

**State cost**: 532,584 pooled observations over the 7-month range (`2025-12-31`–`2026-07-31`) is
the implied retained-window size if MAD-based tolerance is adopted as specified here — a real
operational cost (no O(1) streaming update, unlike Welford), flagged for the recommendation
per this section's own note, not resolved here.

Output: `results/ibkr_massive_mad/dispersion/sigma_vs_mad.parquet` (pooled `ALL` row plus one row
per field, both raw and trimmed estimators).

---

## E4 — k → Databento spend

**Why:** k is no longer only a false-positive dial; it sets paid spend. And flags
cluster, so flag count is the wrong unit — Databento bills against contiguous
ranges.

**Method:** Sweep `k` over the configured grid. For each k:
- count flagged bars;
- cluster flags into contiguous ranges with a gap-merge parameter `g` (sweep `g`
  too — e.g. 5 / 15 / 60 minutes);
- report **range count** and **total minutes billed**, alongside flag count.

**Output:** `e4_budget.parquet` — a (k, g) → (flags, ranges, billed_minutes) table.
This is the exchange rate that turns k into a budget.

---

## E5 — Stationarity (highest-value experiment)

**Why:** The entire backfill plan rests on the assumption that a band calibrated on
recent data is valid on older data. Vendor historical archives are frequently
reconstructed differently from the live feed. This test fails cheaply if it fails.

**Method:** Calibrate `k` on the most recent month. Apply that **fixed** k to each
earlier month and measure flag rate per month per ticker.

**Output:** `e5_stationarity.parquet`, flag-rate-by-month plot with the calibration
month marked.

**Gate:**
- **Flat** → one global band works. Best case.
- **Rising as you go back** → time-varying band, or a hard trust cutoff date beyond
  which manual resolution isn't economically viable. Either way this changes the
  backfill scope and must surface in the recommendation.
- Cross-reference any inflection against E2's step dates before concluding it's a
  reconstruction difference rather than an unhandled corporate action.

---

## E6 — Semi-labeled validation on the overlap month

**Why:** The one month with three providers is the only evaluation set that exists.
It is the difference between k chosen from evidence and k chosen from aesthetics.

**Method:** Restrict to the overlap month. Filter yfinance rows to `ACCEPTED`
validity (existing whistleblower validity gate) before use.

- **Precision proxy:** of MAD-flagged bars, what fraction does yfinance side
  decisively with one provider on (real discrepancy) vs. sit between the two
  (noise)? Define "decisively" as a configured multiple of the yfinance-vs-midpoint
  typical deviation; record the definition.
- **Recall proxy:** of bars the existing yfinance whistleblower flags against IBKR,
  what fraction does the IBKR/Massive MAD band also catch?

**Output:** `e6_validation.parquet`, precision/recall proxies as a function of k.

**E6b — Is yfinance actually a third corner?** Both proxies assume yfinance is
independent of Massive. If Yahoo and Polygon both derive from the consolidated tape,
the third corner is much weaker than it looks and both proxies are inflated.

Test on the overlap month: compute pairwise difference series for all three pairs
(IBKR−Massive, IBKR−yfinance, Massive−yfinance) and correlate them. If
`Massive − yfinance` is materially tighter than `IBKR − yfinance` and
`IBKR − Massive`, they share an upstream. Report the three dispersions side by side.

**Output:** `e6_validation.parquet`, `e6b_independence.parquet`.

**Framing:** These are bounded proxies, **not ground truth**. yfinance remains a
whistleblower and its disagreement is evidence, not verdict. Do not report these as
precision/recall without the qualifier. If E6b shows shared upstream, weaken the
framing further and say so explicitly in findings.

**Gate:** Pick the recommended k from the intersection of E6 (quality) and E4
(spend). State both numbers in the recommendation.

---

## E7 — Volume

**Why:** IBKR `TRADES` is not the consolidated tape. Systematic disagreement with
Massive is expected and will manufacture flags forever if forced into the same band
as price.

**Method:** Own field group. Distribution of `log(massive.volume / ibkr.volume)`
per ticker, by session segment.

**Output:** `e7_volume.parquet`, distribution summary + plot.

**Gate:**
- Stable center, modest dispersion → separate k on the log-ratio; recommend
  parameters.
- Unstable → **recommend excluding volume from reconciliation entirely** and say so
  plainly.

---

## E8 — Replace or coexist

**Why:** Two tolerance mechanisms running on overlapping periods is a maintenance
liability. Decide now rather than discovering it later.

**Method:** On the overlap month, at the recommended k, compare the flag set from
the IBKR/Massive MAD band against the flag set from the IBKR/yfinance Welford band.
Report the confusion matrix of the two flag sets.

**Output:** `e8_overlap.parquet`, set-overlap summary.

**Scope of "replace":** this decides which band is the **primary whistleblower for
the pre-overlap historical period**. It does *not* decide whether yfinance stays in
the pipeline — that is settled by the structural limitation above and is not on the
table here.

**Circularity warning — state this in findings.** E6 and E8 are both computed *using*
yfinance. A "superset" result was therefore derived from the source it would appear
to justify removing, and it holds for one month, one alignment regime, one k. Treat
it as a claim about band coverage, not about yfinance's necessity.

**Gate:**
- **MAD flags are a superset** → recommend the MAD band as primary for the
  historical period. `materiality_floor_tolerance.md` and `variance_floor_clamp.md`
  may become retirable *for that period*; flag them for review, do not assume.
- **Substantially disjoint** → the two bands detect different failure modes;
  coexistence is *earned*. Recommend it explicitly with the evidence, and specify
  which band owns which period.

---

## Role of yfinance after this task

yfinance is **not** retired if this method validates. Its role narrows and shifts:

1. **Rolling calibration set.** The one-month window is *perpetually refreshing*, not
   fixed. It is the only mechanism for recalibrating `k` going forward. Databento
   cannot substitute — it is paid and sparse, usable as an oracle on already-flagged
   bars but never as a rolling sample to measure flag-rate quality against. Without
   yfinance the band has no feedback path and drifts silently until a bad promotion
   surfaces it.
2. **Correlated-error detector on recent data.** The only visibility into IBKR and
   Massive agreeing while both wrong. Subject to the E6b independence caveat.
3. **Intra-provider MAD sanitizer stays unchanged.** That check is about yfinance's
   *own* bad bars and is orthogonal to everything in this task.

What it stops being: the primary whistleblower for the historical period. The MAD
band takes that.

Marginal cost of retention is near zero — already built, already ingesting.

**Add to the recommendation:** a proposed recalibration cadence for `k` against the
rolling window, and the drift magnitude that should trigger review.

## Final deliverable

`results/ibkr_massive_mad/findings.md`, assembled by `report.py`, containing:

1. E0–E2 corrections applied, and whether any of them invalidate the approach.
2. E3 verdict: MAD vs Welford, with the tail-fatness ratio and the implied window
   state cost.
3. Recommended `k` and `g`, with the E4 spend figure and E6 quality figures beside
   them.
4. E5 verdict: one global band, time-varying band, or trust cutoff date.
5. E7 verdict on volume.
6. E6b verdict: is yfinance independent enough to be a third corner, and how much
   does that discount the E6 proxies?
7. E8 verdict: which band is primary for the historical period, with the circularity
   caveat stated.
8. Proposed `k` recalibration cadence against the rolling yfinance window.
9. **A shortlist of Databento ranges** — ticker, start, end, billed minutes — sized
   to the available credit, ordered by flag density.

Open questions and anything that escalated go at the top, not the bottom.

## Follow-up tasks to create (do not implement here)

- Production tolerance change, if E3/E6 support it.
- Databento as a sparse `dim_provider` at Pass 2 — lands in staging under its own
  provider row so manual resolution stays replayable; sparse coverage expressed via
  `feed_coverage`.
- Adjustment dimension, if E2 outcome 2.
- Retirement review of `materiality_floor_tolerance.md` / `variance_floor_clamp.md`,
  scoped to the historical period only, if E8 says replace.
- Rolling `k` recalibration against the refreshing yfinance window, at the cadence
  E6 recommends.
