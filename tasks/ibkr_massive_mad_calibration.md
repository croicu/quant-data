# Task: IBKR/Massive MAD Calibration Experiment

**Status:** E0-E6 done and merged (gate passed; lag 0; no adjustment mismatch; MAD switch
supported but raw-MAD-degenerate; k→spend exchange rate on "conditional MAD"; flat, not rising
going back; recommend k=3.0, well-supported after the overlap-window extension to 26 days/7,420
bars — see E6's own status note for the real float32 data-quality catch and the E6b independence
finding). `config.END_DATE` extended from `2026-07-31` to `2026-08-21` as part of E6's work;
E0-E5 all re-run and re-verified against the wider range (see each section's "Rerun 2026-08-25"
note). E7 done (stable + modest center, but a real fat tail — recommend k_volume≈18.8, much
larger than the price band's k=3.0 — see E7's own status note); E8 not started.
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

**Rerun 2026-08-25 after the range extension to `2026-08-21`** (see E6's status note for why —
this ran `quant-ingest`+`quant-stage` for real, widening the frozen dataset). Result essentially
unchanged: 161 trading days, RTH `both` coverage **99.379%** — still passes, `massive_only` still
never occurs. E0's conclusion holds under the wider range.

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

**Rerun 2026-08-25 after the range extension to `2026-08-21`**: essentially unchanged. Lag 0 still
wins everywhere with the same clear-spike margins; volume hypothesis still rejected (R² 0.0007 for
high, 0.0024 for low — both still ~0). E1's conclusions hold under the wider range.

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

**Rerun 2026-08-25 after the range extension to `2026-08-21`**: unchanged — still exactly 1.0 on
every one of 160 trading days, 0 deviating days.

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

**Rerun 2026-08-25 after the range extension to `2026-08-21`**: essentially unchanged — σ
collapses **-92.43%** under trimming (585,088 pooled observations now), raw MAD still exactly 0.0.
Same conclusion, same caveat.

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

**Dispersion-basis deviation, confirmed with the repo owner before implementation**: E3 found raw
pooled/per-field MAD is exactly 0.0 (more than half of all bars match exactly on every field), so
a literal `k × 1.4826×MAD` band would flag every non-exact-match bar regardless of `k` — the
k-sweep would do nothing. **Fixed by using "conditional MAD" instead**: median(`|d|`) computed only
among bars where `d ≠ 0` for that field (excludes the exact-match point mass), scaled by 1.4826 as
usual. A bar is flagged if **any** of open/high/low/close exceeds `k × conditional_MAD` for that
field — mirrors `reconcile.algorithm._agrees_within_tolerance`'s own per-field-independent
structure. **Every later experiment that references "the band" (E5, E6, E8) must use this same
conditional-MAD basis, not the raw pooled MAD from E3** — carry this forward, don't silently
re-derive a different definition.

**Status: done.** Run: `.exp/budget/k_sweep.py`. Ticker: SPY, 133,146 lag-0-joined bars.
Conditional MAD (scaled, fractional) per field: open 7.84e-6, high 5.14e-6, low 4.99e-6, close
7.89e-6 — notably `open`/`close` have *larger* conditional MAD than `high`/`low` here (the
opposite of what "yfinance noise concentrates in high/low" trained everyone to expect from the
existing production tolerance work) — worth a second look before assuming that prior applies to
the `ibkr`/`massive` pair too.

Flag rate drops smoothly from 15.9% at k=1 to 0.074% at k=20. **At production's own default
(`DEFAULT_RECONCILE_K = 3.0`)**: 1,473 flagged bars (1.106%), clustering into 1,063 ranges
(g=5)/890 (g=15)/528 (g=60), billing 1,723/3,310/14,968 minutes respectively — the g=60 case
costs roughly 8.7x the g=5 case in billed minutes for the same flag set, purely from how
aggressively nearby flags get merged into one paid pull. Full (k, g) → (flags, ranges,
billed_minutes) exchange-rate table in `results/ibkr_massive_mad/budget/k_g_budget.parquet`.
**Recommended k not yet chosen here** — E4 only produces the exchange rate; E6 picks k from the
intersection of this spend curve and E6's own quality proxy, per that section's gate.

**Dollar cost, added same session (repo owner supplied Databento's rate)**: schema is **OHLCV-1m**
(repo owner's call), record size confirmed live against `databento/dbn`'s Rust source
(`record.rs`) — `RecordHeader` (16 bytes) + `OhlcvMsg`'s open/high/low/close/volume (five 8-byte
fields) = **56 bytes/record**, one record per symbol-minute. At the repo owner's stated
**$35.00/GB**, the entire explored (k, g) grid costs **$0.0002 – $0.24 *per ticker*** for SPY over
the 7-month range — at k=3/g=5 (production's default), **$0.0034/ticker**. **This is the actual
headline finding**: for the OHLCV-1m schema specifically, this section's own "why" ("k is no
longer only a false-positive dial; it sets paid spend") turns out to be true in principle but
practically negligible in dollars — Databento spend is not a meaningful constraint on choosing `k`
for this schema. `k`/`g` should be chosen on quality grounds (E6) essentially unconstrained by
budget, *unless* a richer schema (trades/quotes, for a more authoritative oracle read than another
OHLCV-1m bar) is used instead later — that would change this conclusion by orders of magnitude and
hasn't been evaluated. `config.py` gained `DATABENTO_OHLCV_1M_BYTES_PER_RECORD=56`,
`DATABENTO_PRICE_PER_GB=35.00`; `k_g_budget.parquet` gained a `cost_usd` column.

**Per-ticker caveat, flagged when the repo owner pointed out the multi-ticker case**: the figures
above are for SPY alone — real total spend multiplies by however many tickers actually get
flagged through this. Live `dim_ticker` check (2026-08-25): at the time this was written, the
universe was just DIA, QQQ, SPY (3 tickers); **`dim_ticker` was since reseeded the same session to
8** (SPY, SH, QQQ, PSQ, DIA, DOG, IWM, RWM, at the repo owner's explicit request) — but that's only
the dimension row, **none of the 5 newly-added tickers have any actual staging/fact data**, so the
"unverified for non-SPY tickers" caveat below still applies unchanged. Naively scaling SPY's rate
by ticker count stays trivial regardless (even ×8 is nowhere near a real constraint) — but that
assumes every ticker disagrees at SPY's rate, which is **unverified**: no other ticker has a frozen
unpurged staging window the way SPY does (see the Preliminary section), so their own
conditional-MAD/flag-rate has never actually been measured. Repo owner's explicit call: note this
caveat rather than build other tickers' own frozen datasets to measure it for real — the conclusion
("Databento isn't budget-constrained for OHLCV-1m") is robust to it either way given how cheap even
the worst explored case is.

**Rerun 2026-08-25 after the range extension to `2026-08-21`**: essentially unchanged. Conditional
MAD per field: open 7.55e-6, high 4.94e-6, low 4.73e-6, close 7.65e-6 (same `open`/`close` >
`high`/`low` pattern). At k=3: 2,030 flagged (1.388%, up slightly from 1.106% — expected, more
data), billing 2,436/4,994/24,075 minutes (g=5/15/60). Cost range now **$0.0002 – $0.27/ticker**
(was $0.0002–$0.24) — same conclusion, spend still negligible everywhere in the grid.

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

**Status: done — flat, no rising-going-back pattern (good result).** Run:
`.exp/stationarity/monthly_flag_rate.py`. Ticker: SPY. **Superseded numbers below reflect the
2026-08-25 range extension to `2026-08-21`** (see E6's status note for why) — the original
July-calibration run (5 months narrower) is kept out of this section entirely rather than left to
confuse a future read, since the calibration month itself changed and the two runs aren't
directly comparable point-by-point.

Band calibrated on the most recent month (now **2026-08**, a partial month — 13,126 bars, still
above the 5,000-bar low-sample threshold) using the same conditional-MAD basis as E4, `k` fixed at
**3.0**, applied unchanged to each earlier month. December 2025 still produces zero lag-0-joined
bars (`massive` starts `2026-01-02`) so it's absent; 8 months measured, `2026-01`–`2026-08`.

Flag rate across all 8 months (all ≥13k bars, no low-sample months): 7.12% (Jan) → 7.02% (Feb) →
**8.39% (Mar, highest)** → 6.66% (Apr) → **5.66% (May, lowest)** → 5.98% (Jun) → 5.68% (Jul) →
5.60% (Aug, calibration month). Notably higher across the board than the pre-extension run (was
0.7–1.9%) — expected, not a red flag: August's own conditional MAD (the new calibration basis) is
roughly half of July's, so every month's threshold got tighter and flag rates rose accordingly;
recalibrating monthly is exactly what E5 tests the safety of, and this shift is why. **The
earliest month (Jan, 7.12%) is neither the highest nor the lowest** — genuinely flat, no
systematic temporal-drift pattern; the actual question this gate cares about reads the same as
before the extension, now on materially more data (8 months vs 7, and every month's own sample
grew). March remains the relative outlier (~25% above the mean, down from ~2x before — the wider
range diluted its apparent severity) — still not explained by E2 (zero step/split dates for SPY),
still an open question, not investigated further. Plot:
`results/ibkr_massive_mad/stationarity/spy_flag_rate_by_month.png`. Output:
`results/ibkr_massive_mad/stationarity/monthly_flag_rate.parquet`. `config.py` gained
`E5_K_FIXED=3.0`, `E5_MIN_BARS_FOR_FULL_CONFIDENCE=5000`.

**Caveat carried from E4's own per-ticker note**: only SPY has been measured here (the only ticker
with a frozen unpurged window) — this stationarity read doesn't necessarily generalize to the
other 7 tickers now seeded in `dim_ticker` (none have any actual staging/fact data, just the
dimension row).

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

**Status: done — recommend k=3.0 (production's existing default), now on a meaningfully wider
evaluation set.** Run: `.exp/validation/overlap_validation.py`. Ticker: SPY.

**Overlap window — widened 2026-08-25 from 5 days to 26 days, at the repo owner's explicit
request.** Originally `2026-07-27`–`2026-07-31` (1,952 triple-overlap bars) — flagged since
data-prep as thin evidence and never resolved. Fixed for real this session: ran `quant-ingest` +
`quant-stage` (live IBKR Gateway + Massive API calls, real writes to `quant_ingest` and
`staging_market_data_1min`, deliberately not `quant-reconcile`) for SPY `ibkr`+`massive` over
`2026-08-01`–`2026-08-21`, after confirming live that no `quant_schedule` job was enabled (so
nothing would race in and purge the new staging rows), then extended `config.END_DATE` to match.
**Overlap window is now `2026-07-27`–`2026-08-21`, 7,420 triple-overlap bars** (3.8x wider),
`n_whistleblower_flagged=616` (3x more). E0–E5 were re-run against the extended range too — see
each section's own "Rerun 2026-08-25" note; all conclusions held, only E5's absolute numbers
shifted materially (recalibration month changed from July to August).

**Real data-quality finding, caught before it silently corrupted every number below**: `yfinance`'s
stored OHLC values carry **float32 rounding artifacts** (e.g. `737.239990234375` instead of
`737.24`, confirmed by spot-checking raw rows against `ibkr`/`massive`'s clean 2-decimal values) —
a storage-precision quirk somewhere in the ingest/staging path, not real price noise. Uncorrected,
this inflated the "typical yfinance deviation" baseline with sub-cent artifact noise and (much more
seriously) produced a spurious 0.97 naive correlation between `ibkr−yfinance` and
`massive−yfinance` in E6b (see below). Fixed here by rounding `yfinance`'s fields to cent precision
before any comparison — **the underlying production `yfinance` ingest/staging path itself still has
this precision loss; not fixed there, out of scope per this task's Non-goals, worth its own
follow-up issue.**

**E6 precision/recall by k** (`n_whistleblower_flagged=616`; production tolerance, `k=3.0`, real
`provider_pair_disagreement` stddev, `materiality_floor` currently empty/0 on this DB):

| k | field flags | precision | recall |
|---|---|---|---|
| 1 | 2,016 | 50.4% | 84.7% |
| 2 | 304 | 38.2% | 20.9% |
| 3 | 104 | **82.7%** | 6.8% |
| 4 | 49 | 75.5% | 4.2% |
| 5–8 | 48→27 | 70–75% | 4.1→1.8% |
| 10–20 | 20→13 | 75–77% | <1.2% |

**Remarkably close to the 5-day numbers** (k=3 was 84.2%/7.8% before, now 82.7%/6.8% — within
noise) — reassuring, not a coincidence to dismiss: the original thin-sample read held up under
3.8x more data. Recall is now much better-measured at low `k` (84.7% at k=1, up from 62.4% —
smaller samples were understating it). Same shape as before: recall craters fast as `k` rises,
precision stabilizes 75–83% for `k≥3`. Spend still doesn't discriminate (E4). **Recommending
k=3.0** stands, now with substantially more confidence than the original pick: `n=616`
whistleblower-flagged bars over 26 days is real evidence, not the `n=205`/5-day placeholder this
section used to carry. Still not final — the task's own rolling-recalibration cadence (see "Role
of yfinance after this task") means this should keep being revisited as more overlap accumulates,
but it's no longer a low-confidence stopgap.

**E6b — independence check, confirmed on the wider window.** Dispersion test still says **NOT
tighter** — `massive−yfinance`'s conditional MAD (9.46e-6) isn't smaller than `ibkr−massive`'s
(4.53e-6). The naive pooled correlation is still a **construction artifact** (still ~0.97,
mechanically inevitable wherever `ibkr==massive` exactly — see the original finding's explanation,
unchanged). **Restricting to the 5,831 (bar, field) disagreement instances** (up from 1,264) gives
essentially the *same* real finding as the 5-day run: `im_my` = **−0.927** (was −0.921), `im_iy` ≈
−0.006 (was 0.015, still ~none), `iy_my` = 0.380 (was 0.375). **This robustness across a 4.6x
larger disagreement sample is itself worth noting** — `yfinance` tracking `ibkr` on disagreement
bars isn't a small-sample fluke, it's a stable, repeatable pattern. Same conclusion as before:
good news for E6's proxies, not bad — `yfinance`'s vote isn't circular with the `ibkr`/`massive`
comparison being tested. Output: `results/ibkr_massive_mad/validation/e6_validation.parquet`,
`e6b_independence.parquet`. `config.py` gained `WHISTLEBLOWER_PROVIDER="yfinance"`,
`E6_DECISIVE_MULTIPLE=3.0`, `E6B_SHARED_UPSTREAM_MARGIN_PCT=30.0`. `load.py` gained
`fetch_provider_pair_disagreement_stddev` (new read-only query, reused by future experiments if
needed).

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

**Status: done — STABLE + MODEST, recommend a separate volume band, but with a real fat-tail
wrinkle.** Run: `.exp/volume/log_ratio.py`. Ticker: SPY, 146,272 lag-0-joined bars (1 excluded for
zero volume on one side).

**What this is and isn't testing, stated explicitly to avoid a real ambiguity**: production
**does not run any cross-provider comparison on volume today** — `005_remove_volume_field_group`
deliberately removed it as an independently-reconciled field group; a promoted bar's `volume`
just rides along with whichever provider won the `ohlc` vote, zero MAD/Welford check involved.
E7 tests a hypothetical, not current behavior: *if* this task's new historical-period IBKR/Massive
MAD band were extended to cover volume too, would that be viable, or should volume stay excluded
the way it already is? "Unstable" would have confirmed today's exclusion is still right;
"stable + modest" (what follows) means a volume band *could* be built if wanted — evidence for a
possible future follow-up, per this task's own Non-goals ("do not implement the production
tolerance path"), not a description of, or a change to, what reconciliation does today.

Per-segment median log(`massive`/`ibkr`) volume: pre 0.114, RTH 0.105,
post 0.090 — **consistently positive across every segment** (`massive` reports ~10-12% higher
volume than `ibkr` `TRADES` throughout, as expected — `TRADES` isn't the consolidated tape).
Max pairwise center spread **0.024** (threshold ≤1.0) → **STABLE**. Max segment MAD-scaled
dispersion **0.103** (threshold ≤2.0) → **MODEST**. Gate passes: recommend volume gets its own
log-ratio band, separate from the OHLC price band.

**Real wrinkle, same shape as E3's own finding**: a "modest" MAD doesn't rule out a heavy tail —
checked with the identical σ/(1.4826×MAD) diagnostic E3 used, and it comes back **5.62**, far above
E3's own price-series ratio (which was undefined/degenerate from an exact-match point mass — this
one is a genuine, cleanly-measured heavy tail, no point mass involved). Consequence: a coarse
`k` sweep (1–10) only gets flag rate down to 4.07% at `k=10` — nowhere near a price-band-comparable
~1.5% target. Computed the *exact* `k` via empirical percentile rather than accepting the grid's
own ceiling as the answer: **k_volume ≈ 18.8** needed to reach a 1.5% flag rate. **Recommending
k_volume ≈ 18.8** — a much larger multiplier than the price band's k=3.0, exactly because the
distribution is fundamentally fatter-tailed, not because the center is unstable or the typical
spread is large. Plot: `results/ibkr_massive_mad/volume/spy_log_ratio_boxplot.png` (box IQR only,
matches the "modest" MAD read — the tail isn't visible there, only in the σ/MAD ratio and the
k-sweep numbers, so don't read the plot alone as the full picture). Output:
`results/ibkr_massive_mad/volume/log_ratio_summary.parquet`. `config.py` gained
`E7_STABLE_CENTER_SPREAD_MAX=1.0`, `E7_MODEST_DISPERSION_MAX=2.0`, `E7_K_GRID`,
`E7_TARGET_FLAG_RATE_PCT=1.5`.

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
