# Task: IBKR/Massive MAD Calibration Experiment

**Status:** E0-E7 done and merged (gate passed; lag 0; no adjustment mismatch; MAD switch
supported but raw-MAD-degenerate; k→spend exchange rate on "conditional MAD"; flat, not rising
going back; recommend k=3.0, well-supported after the overlap-window extension to 26 days/7,420
bars — see E6's own status note for the real float32 data-quality catch and the E6b independence
finding; stable+modest volume center but a real fat tail, recommend k_volume≈18.8). E8 done
(substantially disjoint — coexistence earned, not MAD-replaces-yfinance; `materiality_floor_
tolerance.md`/`variance_floor_clamp.md` are NOT retirable on this evidence). `config.END_DATE`
extended from `2026-07-31` to `2026-08-21` as part of E6's work; E0-E5 all re-run and re-verified
against the wider range. **All nine experiments (E0-E8) done, `report.py` unblocked, `findings.md`
rebuilt (2026-08-26).** B1-B3 (blocking) and B7 (a real bug B2 depended on) are resolved — see
"Pre-report blockers, resolved" immediately below. B8 resolved in the same pass. **A
second review pass (2026-08-26) raised R1-R5, all now checked/resolved — see
"Second review pass" below.** R1's reclassification worry (B4 blocking the `k` choice)
was checked directly and did NOT hold at k=3.0 (58/42 ibkr/massive split among decisive
flags, not the near-automatic split the worry predicted) — `k=3.0` stands, not
conditional on further B4 work. **R3's pooled-vs-rolling tension is resolved (R3a-R3f):
pool the conditional-MAD scale, keep `k` rolling against yfinance, add a separate drift
monitor that doesn't feed back into the band.** R3d's volatility-normalization hypothesis
(would have dissolved the question instead of deciding it) was checked live against the
warehouse and refuted — January/August are genuine data regimes, not a normalization
artifact. R3f (separate `high`/`low` vs `open`/`close` field grouping) is a real
follow-up, independent of R3d. B4-B6 remain open, non-blocking.
**Type:** Experiment (offline analysis, no production code path)
**Depends on:** One year of ingested IBKR + Massive 1-minute bars already on disk
**Blocks:** `tasks/retroactive_revision.md`, Massive backfill scope, Databento integration decision

---

## Pre-report blockers, resolved (2026-08-26)

Review of the E0–E8 status notes found B1–B3 blocking `report.py`; B4–B6 change what
findings says; B7–B8 are corrections. All four (B1, B2, B3, B7) below are now resolved
and reflected in `results/ibkr_massive_mad/findings.md` (rebuilt) and, for B7, in
`.exp/validation/overlap_validation.py` (fixed and re-run). Nothing here invalidates
E0–E2. B8 is resolved too (see its own note). **A second review pass checked whether B4
should block the `k` choice specifically (R1) — it does not, at k=3.0** (see R1's
resolution below). B4–B6 remain open and non-blocking.

Note: B7 appears twice below — once as the resolution and once as the original
statement. Left as-is deliberately so the original diagnosis stays readable next to what
it turned out to be.

### B1 — "k=3" names two different thresholds — RESOLVED, not a bug

Verified directly against the warehouse (own-threshold flag rate per month, and August's
conditional MAD vs the full-range pooled MAD E4 uses): E4's ~1.1–1.4% and E5's 5.6–8.4%
were never meant to be the same number — E4 pools conditional MAD over the full 8-month
range, E5 deliberately recalibrates on August alone and freezes that. August's own
per-field conditional MAD (3.6–4.8e-6) is ~1.4–2.1x smaller than the full-range pooled
MAD (4.7–7.7e-6), which alone would tighten the band. But the internal-consistency worry
(July's own MAD ~2x August's, yet July and August land at nearly the same fixed-threshold
flag rate, 5.68% vs 5.60%) is real and has a second, independent cause: **August 2026 is
itself an atypically fat-tailed month relative to its own scale, not just a smaller-scale
one.** Evaluated self-referentially (each month against its own threshold), August flags
5.60% of its own bars; April–July flag only 0.86–1.40% of their own bars. Both effects
(tighter absolute threshold + August's own heavier relative tail) push every month's
fixed-threshold flag rate up together, which is why July and August converge under the
shared August threshold despite differing "typical" scale. **The flat verdict itself is
unaffected** (a claim about trend, not absolute level — Jan is still neither highest nor
lowest), but the 5.6–8.4% absolute numbers are now correctly attributed to calibrating on
an atypical month, not treated as a stable property of the underlying disagreement (which,
checked month-by-month, actually ranges 0.86%–18.1% by each month's own scale). Full
writeup: `findings.md` section 4.

### B2 — `k` was being chosen against the wrong constraint — RESOLVED

Confirmed: E4 already showed spend is negligible everywhere in the grid, so the gate's
stated "intersection of quality and spend" collapses to quality alone, and Pass 2 being
manual/deliberate means the real constraint is human review capacity, which nothing in
E0–E8 quantifies. `findings.md` section 3 now states `k=3.0` explicitly as a
review-capacity choice, not a spend-driven one. **The original recall-first case for
`k=1` (84.7% recall at 50.4% precision) does not survive** — see B7: that 50.4% precision
figure was wrong. Corrected, `k=1`'s precision is ~4.6% (93 genuine hits in 2,016 field
flags, roughly 1 in 22), which makes `k=3.0`'s 82.7% precision / 6.8% recall tradeoff the
stronger pick on the numbers actually available, not just the convenient one. Full table:
`findings.md` section 3.

### B3 — The headline finding was missing from the document — RESOLVED

`findings.md`'s "Open questions and escalations" section now leads with it as item 1: the
pre-overlap historical period is roughly an order of magnitude less protected than the
whistleblower-covered period (MAD band catches only ~6.8% of what yfinance catches on the
overlap window), and Databento cannot close that gap since it's only ever invoked on bars
the band already flagged. Framed explicitly as a capability bound on the whole backfill,
not an E8 footnote — flagged as something `tasks/retroactive_revision.md`'s scope should
reflect, not just note.

### B7 — E6 precision non-monotonic at k=2 — RESOLVED, real bug found and fixed

Root cause: `overlap_validation.py`'s "decisive" precision proxy computed its `typical`
yfinance-deviation baseline as a plain median over *all* triple-overlap (bar, field)
instances. 82–89% of those are exact ties (yfinance matches one candidate to the cent, a
byproduct of E6's own float32-rounding fix), so the median silently collapsed to exactly
`0.0` for every field — not "near zero," exactly zero. With `typical=0.0`, "decisive"
silently degenerated to "closer deviation is an exact tie AND farther deviation is any
nonzero value" instead of a real noise-scaled threshold, which is what produced the
originally-reported non-monotonic curve (50.4% / 38.2% / 82.7% at k=1/2/3). **Fixed** by
using the conditional (nonzero-only) median instead — the same convention this task
already uses for conditional MAD elsewhere, for the same reason (majority-zero point
mass). Corrected curve (monotonic through k=3, per E6's now-fixed `e6_validation.parquet`):
4.6% / 30.6% / 82.7% / 75.5% / 75.0% / 73.5% / 70.4% / 75.0% / 76.9% / 76.9% at
k=1/2/3/4/5/6/8/10/15/20. k=3's own number happens to be unchanged; k=1 and k=2 were both
substantially wrong. `ruff`/`pytest` clean after the fix (391 passed).

### B4 — E6b's result undercuts E6's precision proxy — checked at k=3.0, not blocking (see R1)

`im_my = −0.927` says Massive's deviation dominates both difference series on
disagreement bars — equivalently, yfinance sits close to IBKR on exactly those bars.
That is fine for the question E6b asked (no shared upstream with Massive) and the
"not circular" conclusion stands.

But E6's precision proxy counts bars where yfinance sides decisively with *one*
provider. If yfinance tracks IBKR on the bars being scored, then 82.7% precision
substantially measures yfinance–IBKR affinity rather than which provider was right.

**Action:** report the sided-with-IBKR vs sided-with-Massive split among precision-
positive bars. If lopsided toward IBKR, discount the proxy and weaken the framing —
E6b's own instruction ("if E6b shows shared upstream, weaken the framing further")
applies here in a direction it did not anticipate.

### B5 — E6b has an unexploited three-cornered-hat solution

E6b reports `massive−yfinance` conditional MAD at 9.46e-6 and `ibkr−massive` at
4.53e-6, but never reports `ibkr−yfinance`.

With all three pairwise dispersions and three providers, **per-provider error variance
becomes identifiable** — the standard three-clock solve. On the overlap window this
converts `preferredProvider = ibkr` from a stated belief into a derived claim, which
is a question this project has carried unresolved since the two-provider era. The
−0.927 already hints the answer is that Massive carries the larger share.

Cheap: one more dispersion from data already loaded in `.exp/validation/`. Scope note:
the result holds for the overlap window only and inherits B4's caveat if yfinance and
IBKR turn out to be less independent than assumed.

### B6 — float32 may have contaminated production, not just this experiment

E6 scopes the yfinance float32 rounding artifact to a follow-up issue. But
`provider_pair_disagreement` stddev has been accumulating over yfinance values
carrying sub-cent artifact noise for as long as that ingest path has existed.

That makes it a possible **invalidation of existing production Welford state**, not
only a new bug to file. Check whether stored stddev needs recomputation after the
ingest fix, and note the interaction with E6's other observation that
`materiality_floor` is currently empty/0 on this DB.

### B7 — E6 precision is non-monotonic at k=2

50.4% (k=1) → 38.2% (k=2) → 82.7% (k=3). Precision should rise as the band tightens.
At n=304 field flags this is not sample noise. Suspect the decisive-multiple logic or
a per-field vs. per-bar counting inconsistency. Fix before the table is published.

### B8 — E7's `k_volume ≈ 18.8` is a quantile, not a MAD multiple

It was derived by inverting to a 1.5% target flag rate via empirical percentile. At
that multiplier the 1.4826 scaling carries no distributional meaning. State it as an
empirical quantile of the log-ratio distribution — that is more honest and more
directly reusable than a multiplier that only looks like the price band's `k`.

**RESOLVED, same pass as B1-B3/B7**: `findings.md` section 3 now states this explicitly
— "an empirical quantile of the log-ratio distribution ... not a MAD multiple in the same
sense as the OHLC k."

---

## Second review pass (2026-08-26, after B1–B3/B7/B8 resolution) — R1-R5 RESOLVED

**R3's pooled-vs-rolling tension is now resolved (R3a–R3f, appended to R3).** Short
version: pool the scale, keep rolling `k`, add a drift monitor, and test the
intrabar-range normalization hypothesis first — it may dissolve the question rather than
decide it.

Review of the resolutions above. B7's root cause is a genuine find — a median
collapsing to exactly zero on a majority-tie sample is the same failure shape as E3's
raw MAD, and catching it twice in this task is a good sign about the method. R1 is the
one that matters; R2–R4 change what `findings.md` should say; R5 is a verification. All
five checked live against the warehouse; results below. `findings.md` section 3
(R1/R2/R4), section 4 (R3), and section 8 (R3's tension) all updated to match.

### R1 — B2's resolution depends on B4, which is filed non-blocking — CHECKED, not blocking at k=3

Verified directly: among k=3's 86 decisive field flags, yfinance sides with `ibkr` on
58.1% (50) and `massive` on 41.9% (36). That's a real lean toward `ibkr` — not nothing —
but far short of the near-automatic 90%+ split the "decisive fires near-automatically"
worry in R1 would predict if the proxy were purely measuring yfinance–IBKR affinity
rather than genuine correctness. The lean **does** widen sharply with `k`: 59% at k=1–2,
58% at k=3, rising to 84% at k=8 and 93% at k=10 — so B4's concern is real and gets worse
at the tail of the grid, but at the recommended k=3.0 specifically it is not the
dominant driver of the 82.7% number. **`k=3.0` does not need to be held conditional on
further B4 work.** B4 stays open as a general follow-up (its own three-cornered-hat
angle, R5's own note about B7 mirroring E3's raw-MAD failure shape) but is *not*
reclassified as blocking — the specific worry motivating that reclassification didn't
hold up at the operating point actually recommended. The high-k end of the grid (k≥8)
should be read with more caution than k=3 itself.

### R2 — The two proxies now disagree about `k=1`; picking one is not resolving it — RESOLVED

Confirmed: precision is counted per (bar, field), recall per bar — different
denominators, not an error. `findings.md` section 3 now states this explicitly and adds
the marginal-discovery framing: k=1 nets 93 genuine (bar, field) hits against k=3's 86 —
**only 7 more genuine hits for ~19x the review volume (2,016 vs 104 field flags)** — a
cleaner, unit-consistent argument for k=3.0 than comparing the precision percentages
directly.

### R3 — B1's real consequence isn't drawn out, and it contradicts a follow-up — INVESTIGATED, then RESOLVED (see R3 resolution below)

**The 18.1% figure is January, not March** — checked directly. It is not conditional-MAD
sampling noise: January's own-threshold rate (18.07%, n=18,388) is driven almost
entirely by `high`/`low` (~9.4–9.7% each) while `open`/`close` barely flag (~0.3% each),
and it is sustained through the whole month (day range 12.6%–23.1%, first-5-days average
18.8% vs. the rest at 17.8% — no onboarding-artifact front-loading). **This looks like a
real, sustained monthly regime difference in `high`/`low` agreement, not an estimator
noise artifact** — the opposite of R3's original hypothesis ("conditional MAD is a noisy
estimator of a quantity whose tail is actually stable"). March's own outlier (open
question #5, findings.md) is a separate, smaller anomaly (1.41% own-threshold) and is
**NOT** closed by this — that question stays open.

Because January's elevated tail is real rather than noise, the tension R3 raised doesn't
resolve cleanly in either direction: a rolling monthly recalibration is genuinely exposed
to landing on an atypical-but-real month (as already seen with both January and August)
and inheriting its real, non-spurious looseness — but pooling over the full range can't
detect genuine drift either, which is the entire reason E5 exists in the first place.
**This is a real design choice between the two calibration bases E0-E8 already computed
(pooled vs. rolling-month), not a bug — left for the repo owner to decide before the
recalibration-cadence follow-up (Pending Tasks) is actually built, rather than picked
unilaterally here.** `findings.md` sections 4 and 8 both state the tension explicitly.

#### R3 resolution (2026-08-26) — three things could roll, and they have opposite answers

The tension is partly an artifact of the framing. "Pooled vs rolling" was treated as one
choice, but three separate quantities could roll:

1. the conditional-MAD **scale** (the dispersion estimate),
2. the multiplier **k**,
3. neither.

E5's stationarity test rolled the **scale** (August's own MAD, frozen, applied backward).
The Pending Tasks follow-up rolls **k** against the refreshing yfinance window. R3 read
these as the same decision. They are not, and they resolve in opposite directions.

**R3a — Pool the scale. Rolling it destroys the drift signal.**

If the band recalibrates to each month's own dispersion, then by construction every month
flags near its own design rate and drift becomes invisible. E5 exists to test
stationarity; adopting rolling scale calibration makes that property permanently
untestable in production.

This is the standard control-chart argument: control limits are fixed from a reference
period precisely so the chart can fire. Recompute the limits from the data being
monitored and it never can. Decisive for the scale.

**R3b — Keep rolling `k`. It is a different animal.**

Re-picking the operating point against fresh yfinance labels does not touch the
dispersion estimate; it re-chooses where to sit on a curve whose shape E6 measures. The
follow-up stands. Caveat to record: `k` rolled on recent data is applied to a historical
period with no labels, so it inherits E5's flatness result as an assumption rather than
being validated directly there.

**R3c — The asymmetry seals it.**

Pooled is wrong in a *knowable* direction: too loose in quiet months, too tight in
volatile ones, and which is which is observable. Rolling is wrong in an *unknowable*
direction — it silently adapts to degradation, so a provider getting worse produces a
wider band and an unchanged flag rate. For a whistleblower, prefer the failure mode you
can see.

**R3d — Test the premise first: January's signature may dissolve it.**

`high`/`low` at ~9.5% each while `open`/`close` sit at ~0.3% is precisely what you would
expect if disagreement scales with **intrabar range** rather than price level. Extremal
fields have room to differ in proportion to how far the bar travelled; `open`/`close` are
single prints at fixed times and do not. A high-volatility month then produces exactly
January's pattern with no data-quality regime change at all.

The difference series currently uses `d = (ibkr − massive) / midpoint` — price-level
normalization for every field. For extremal fields that is arguably the wrong
denominator.

Two cheap checks against data already loaded:

- Per-bar, correlate `|d_high|` against intrabar range `(high − low) / midpoint`. Strong
  positive correlation confirms the mechanism.
- Regress each month's own-threshold flag rate on that month's realized volatility. If
  January and August are the high-volatility months, the 0.86%–18.1% spread is largely
  explained.

**If it holds:** renormalize `high`/`low` by intrabar range instead of price level and
re-run E5. The month-to-month spread should collapse and the pooled-vs-rolling tension
largely *dissolves* rather than being decided — the variation was unmodeled volatility
scaling, not a regime difference. **If it does not hold:** January is a genuine data
regime, and R3a/R3c stand as the answer.

**R3d checked live (2026-08-26) — TESTED, does NOT hold.** Both proposed checks run
against the real warehouse data:

- **Per-bar correlation** between `|d_high|`/`|d_low|` and intrabar range
  `(high−low)/midpoint` (ibkr's own high/low as the volatility proxy): essentially zero.
  Pooled: pearson r=0.0044 (`high`), 0.0033 (`low`). Restricted to disagreement-only bars
  (the subset where the mechanism, if real, should show up most clearly): r=0.0094
  (`high`), 0.0127 (`low`), spearman ρ=0.011/0.026. No relationship, in either direction.
  (`open`/`close` show a *stronger* correlation than `high`/`low` in this same check —
  0.11–0.42 depending on measure — which is the opposite of what the mechanism predicts,
  since `open`/`close` are the fields the hypothesis says should be *unaffected*.)
- **Month-level regression**: own-threshold flag rate vs. that month's mean intrabar
  range gives r=−0.32 (wrong sign — higher volatility associated with *less*
  disagreement, if anything) and R²=0.10 (essentially no explanatory power). Decisively:
  **August has the *lowest* mean intrabar range of all 8 months (0.000190) despite being
  the second-most-elevated month for disagreement (5.60% own-threshold)**; March has the
  *highest* range (0.000486) but one of the *lowest* disagreement rates (1.41%). January
  and August are not the high-volatility months this hypothesis needs them to be.

**Conclusion: the volatility-scaling mechanism is refuted, not just unconfirmed.** Per
R3d's own stated branching, this means **January (and August) are genuine data regimes,
not an artifact of price-level normalization — R3a/R3c stand as the answer, not the
renormalize-and-dissolve branch.** No renormalization or E5 re-run is warranted on this
evidence; don't build it speculatively. R3f's field-grouping question (below) is
unaffected by this either way and still stands on its own.

**R3e — Recommendation, now unconditional (not "either way" — R3d's test settled which branch applies).**

Pooled scale for the band, plus a **separate monitor** that recomputes each month's own
conditional MAD and own-threshold rate and *alerts* on deviation **without changing the
band**. This buys drift detection without exposing the band to it. Drift then triggers a
human recalibration decision, which is the right place for it given Pass 2 is already
deliberate.

**R3f — Consequence for field grouping.**

E6/E7 already treat volume as its own field group with its own `k`. January suggests
`high`/`low` and `open`/`close` may need the same separation on the price side,
independent of how the normalization question in R3d lands. Worth deciding alongside it.

### R4 — Precision has no null baseline — RESOLVED, and the naive 8.3% baseline was itself wrong

Computed the correct like-for-like comparator: the unconditional "decisive" rate over
*all* 29,680 (bar, field) instances in the overlap window (not just MAD-flagged ones) is
**0.313%** — not the ~8.3% naively guessed in the original R4 note (which used the
whistleblower's bar-level flag rate, a different denominator than precision's per-field
one, the same units mismatch R2 flags). Every k in the grid clears the *correct* 0.313%
baseline by a wide margin: k=1's corrected 4.6% is ~15x it, k=3's 82.7% is ~264x it. **No
operating point tested is anywhere near indistinguishable from chance** — k=1 is not
below chance, contrary to what R4 worried might be the case.

### R5 — Verify the B7 fix propagated — CONFIRMED, genuine, not a coincidence

Checked field-by-field at k=3: the decisive (bar, field) set under the fixed
(conditional-median) definition and the buggy (plain-median, typical=0) definition is
**exactly identical** — 86/104 both ways, zero mismatches in any field among the flagged
set. This is for the reason expected, not coincidence: at k=3 every flagged difference is
large enough that "closer ≤ typical" resolves the same way whether `typical` is 0.0 or
the real ~6.5e-6 conditional median — the boundary case where the bug and the fix would
diverge only bites at smaller, near-tie differences, which k=3's threshold already
excludes. The fix is genuinely propagated, confirmed by mechanism, not just by an
unchanged number.

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

**Status: done — substantially disjoint, coexistence earned.** Run:
`.exp/coexistence/flag_set_overlap.py`. Ticker: SPY, same 26-day overlap window as E6
(`2026-07-27`–`2026-08-21`, 7,420 bars), at the recommended `k=3.0`.

**Confusion matrix**: both flagged 42, MAD-only 58, whistleblower-only **574**, neither 6,746.
MAD band total flagged 100; whistleblower total flagged 616. **Jaccard = 0.062**; MAD misses
**93.2%** of what the whistleblower flags (threshold for "superset" was ≤10% missed) — nowhere
close. This isn't a new finding so much as E6's own recall number (6.8% at k=3) formalized into
the confusion-matrix framing the gate asks for — the two numbers say the same thing from different
angles and agree.

**Verdict: SUBSTANTIALLY DISJOINT — coexistence is earned, not MAD-replaces-yfinance.** The two
bands detect different failure modes. Recommending: the MAD band owns the pre-overlap historical
period (no `yfinance` available there at all — this was never in competition for that period
anyway), the existing `yfinance`/Welford band keeps owning the recent/rolling period it already
covers. **`materiality_floor_tolerance.md`/`variance_floor_clamp.md` are NOT retirable** for any
period on this evidence — the disjoint result is exactly the gate outcome that keeps both
mechanisms alive, not the one that would flag them for review.

**Circularity caveat, per the task's own explicit instruction**: this comparison is computed
*using* `yfinance` (same as E6) — stated prominently in the script's own output. Doesn't change the
read here (a disjoint result isn't the direction circularity would bias toward — circularity would
inflate an apparent *superset* finding, not manufacture a disjoint one, so if anything this result
is on the more-trustworthy side of that concern), but recorded per the task's instruction
regardless. Output: `results/ibkr_massive_mad/coexistence/flag_set_overlap.parquet`. `config.py`
gained `E8_RECOMMENDED_K=3.0`, `E8_SUPERSET_MISS_MAX_PCT=10.0`.

---

## Role of yfinance after this task

yfinance is **not** retired if this method validates. Its role narrows and shifts:

1. **Rolling calibration set for `k`.** The one-month window is *perpetually refreshing*,
   not fixed. It is the only mechanism for re-picking the operating point `k` going
   forward. Databento cannot substitute — it is paid and sparse, usable as an oracle on
   already-flagged bars but never as a rolling sample to measure flag-rate quality
   against. **Note per R3a/R3b: this rolls `k` only. The conditional-MAD scale is pooled
   and fixed; drift is caught by the R3e monitor, not by recalibrating the band.**
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
- **yfinance float32 precision loss in the production ingest/staging path** (B6), plus
  an assessment of whether accumulated `provider_pair_disagreement` stddev needs
  recomputation once fixed.
- **Per-provider error attribution via three-cornered hat** (B5), if the overlap-window
  solve proves informative — this is the path to making `preferredProvider` a derived
  claim rather than a stated belief.
- Rolling `k` recalibration against the refreshing yfinance window, at the cadence
  E6 recommends. Belongs in quant-scratch — recurring, runs on a refreshing window,
  needs only flag rates. **Per R3b: this rolls `k` only. The conditional-MAD scale is
  pooled and must not roll** (R3a — a rolling scale makes drift undetectable by
  construction).
- **Drift monitor** (R3e): recomputes each month's own conditional MAD and own-threshold
  flag rate and *alerts* on deviation without changing the band. This is what recovers
  the drift detection that pooling the scale would otherwise cost. Pairs with the item
  above; the alert triggers a human recalibration decision rather than an automatic one.
- ~~Intrabar-range normalization for `high`/`low`~~ (R3d) — **checked live 2026-08-26,
  refuted, not a follow-up**: per-bar correlation between `|d_high|`/`|d_low|` and
  intrabar range is ~0 (r=0.004-0.013 even disagreement-only), and month-level flag rate
  vs. mean intrabar range is r=-0.32 (wrong sign) with August the *lowest*-range month
  despite being the second-most-elevated for disagreement. January/August are genuine
  data regimes, not a volatility-normalization artifact — see R3d's resolution above.
- **Separate field grouping for `high`/`low` vs `open`/`close`** (R3f), independent of
  how R3d lands. January's signature (~9.5% vs ~0.3%) suggests they do not belong under
  one `k`, the same way volume already does not.
