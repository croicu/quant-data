"""Shared parameters for tasks/ibkr_massive_mad_calibration.md's experiments.

Grown incrementally, one experiment at a time -- only what E0 needs is populated here today.
Later experiments (E1's lag grid, E4's k/g sweep, ...) add their own fields when they're actually
implemented, not speculatively ahead of that.
"""

from __future__ import annotations

from datetime import date

# Confirmed live against CroicuWS2 2026-08-25: SPY is the only ticker with an unpurged staging
# window wide enough for this task (quant-reconcile deliberately left disabled for this range --
# see tasks/ibkr_massive_mad_calibration.md's memory note). DIA/QQQ only have a handful of
# in-flight staging rows from ordinary ongoing pipeline activity, not a frozen dataset.
TICKERS: list[str] = ["SPY"]

# Widest range of the *frozen* dataset (quant-reconcile deliberately disabled for this range so
# candidate rows stay unpurged). Originally 2025-12-31..2026-07-31; extended to 2026-08-21 on
# 2026-08-25 (repo owner's explicit request) specifically to widen E6's overlap window with
# yfinance, which had rolled forward past the original END_DATE on its own trailing-30-day
# window -- ran quant-ingest + quant-stage for SPY/ibkr+massive over 2026-08-01..2026-08-21
# (deliberately not quant-reconcile, same reasoning as the original backfill: confirmed live
# first that no quant_schedule job was enabled, so nothing would race in and purge these new
# staging rows). Confirmed live post-extension: ibkr 2025-12-31..2026-08-21 (154,560 rows, 172
# distinct days), massive 2026-01-02..2026-08-21 (146,272 rows, 170 distinct days) -- both now
# span the full range with no gap (the old stray 2026-08-03/2026-08-10 rows merged into the
# continuous run). ibkr's extra day at the start is exactly the kind of gap E0 exists to surface,
# not an input error to correct. E0-E6 were originally calibrated against the 2026-07-31 cutoff;
# rerun after this extension, see each experiment's own status note in the task file for any
# resulting changes.
START_DATE: date = date(2025, 12, 31)
END_DATE: date = date(2026, 8, 21)

CANDIDATE_PROVIDERS: tuple[str, str] = ("ibkr", "massive")

# Invariant 6 (tasks/ibkr_massive_mad_calibration.md): pin and record the method, don't silently
# union across methods. staging_market_data_1min carries no method column itself -- stage merges
# each provider's configured methods into one row per (ticker, date, time, provider), with OHLC
# always sourced from that provider's PRIMARY_METHOD_BY_PROVIDER entry
# (quant_data._internal.contracts). For ibkr that's "TRADES", so staging's ibkr OHLC is already
# implicitly pinned to TRADES with no further filtering needed -- this constant exists to make
# that fact explicit and recorded in the manifest, not to filter a query.
IBKR_METHOD: str = "TRADES"

# Extended-hours outer bound used to build the expected-minute grid for E0's "neither" category.
# Not an existing named constant anywhere in src/ (checked -- only 9:30/16:00 exist, as
# reconcile/cli.py's _MARKET_OPEN/_MARKET_CLOSE); 4:00/20:00 describes the actual observed data
# extent on this warehouse, recorded here as this experiment's own assumption.
PRE_POST_GRID_START_HOUR_ET: int = 4
PRE_POST_GRID_END_HOUR_ET: int = 20

# Invariant 4: any sampling must be seeded and recorded in the manifest. No experiment run yet
# actually samples, but the seed is pinned here so the first one that does doesn't have to
# introduce a new config surface for it.
RANDOM_SEED: int = 20260825

# E1 (alignment): lag applied to massive's timestamp when pairing against ibkr(t), in minutes.
# ibkr(t) is compared against massive(t + lag) for each lag in this grid -- see
# tasks/ibkr_massive_mad_calibration.md's E1 section for why +/-1 bracket 0.
ALIGNMENT_LAGS_MINUTES: list[int] = [-1, 0, 1]

# E2 (adjustment mismatch): a day's median ibkr.close/massive.close ratio counts as "deviating"
# once it's off 1.0 by at least this many percentage points -- small enough to catch a real split
# or dividend-adjustment step, large enough to not be tripped by ordinary tick noise (E1 already
# showed high/low can differ by a fraction of a percent even at the correct lag).
ADJUSTMENT_MATERIALITY_PCT: float = 0.5

# A deviating day's ratio counts as "near" one of these multiples (common split ratios and their
# reverse-split reciprocals) when within ADJUSTMENT_JUMP_TOLERANCE_PCT of it, relatively.
ADJUSTMENT_RATIONAL_MULTIPLES: list[float] = [0.2, 0.25, 1.0 / 3.0, 0.5, 2.0 / 3.0, 0.75, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0]
ADJUSTMENT_JUMP_TOLERANCE_PCT: float = 2.0

# E3 (MAD vs Welford): top-tail fraction of |d| dropped for the trimmed recompute, per the task's
# own "top 0.1% of |d| removed" method.
DISPERSION_TRIM_TOP_PCT: float = 0.1

# Heuristic read (not a hard gate -- same spirit as E1's CLEAR_SPIKE_MARGIN_PCT): "ratio near 1.0"
# means <= this; "sigma stable under trimming" means its pct change is within +/- this many points.
# Both must hold to read as "recommend against MAD" per the task's gate wording.
DISPERSION_RATIO_NEAR_ONE_MAX: float = 1.15
DISPERSION_SIGMA_STABLE_PCT: float = 10.0

# E4 (k -> Databento spend): k multiplies each field's *conditional* MAD -- median(|d|) computed
# only among bars where d != 0, scaled by 1.4826, then MAD taken relative to that nonzero
# subsample's own median -- not the raw pooled MAD from E3, which was exactly 0.0 (repo owner's
# explicit call, given E1/E3 both showed match rates well above 50% on every field: a plain MAD
# over the full population -- including the exact-match point mass -- can't ever produce a
# meaningful k sweep). Centered around reconcile's own production default,
# quant_data._internal.shared.settings.DEFAULT_RECONCILE_K = 3.0.
E4_K_GRID: list[float] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0]

# Gap-merge parameter for clustering flagged minutes into contiguous Databento-billable ranges, in
# minutes -- task's own suggested grid.
E4_G_GRID_MINUTES: list[int] = [5, 15, 60]

# E4, continued: turning billed_minutes into dollars. Schema is OHLCV-1m (repo owner's call --
# same aggregate-bar shape already used throughout this task, not raw trades/quotes). Record size
# confirmed live against databento/dbn's Rust source (record.rs, 2026-08-25): RecordHeader (16
# bytes: length/rtype/publisher_id/instrument_id/ts_event) + OhlcvMsg's open/high/low/close/volume
# (five 8-byte fields) = 56 bytes/record, one record per symbol-minute. Price is the repo owner's
# own stated rate; decimal GB (1e9 bytes), Databento's own convention.
DATABENTO_OHLCV_1M_BYTES_PER_RECORD: int = 56
DATABENTO_PRICE_PER_GB: float = 35.00

# E5 (stationarity): fixed k applied to the band calibrated on the most recent month, per that
# section's method. Production's own default (quant_data._internal.shared.settings.
# DEFAULT_RECONCILE_K) -- E3/E4 already anchored their own reporting to this value, so it's the
# natural fixed point here too rather than a new choice. E6 may later pick a different recommended
# k; if it does, this fixed value should be revisited and E5 rerun with it.
E5_K_FIXED: float = 3.0

# A month with fewer than this many lag-0-joined bars is flagged as low-sample in E5's output
# (e.g. a partial calendar month at the range's edge) rather than silently plotted on equal
# footing with a full month.
E5_MIN_BARS_FOR_FULL_CONFIDENCE: int = 5000

# E6 (semi-labeled validation): the whistleblower providing the "overlap month". Widened
# 2026-08-25 (repo owner's request, see END_DATE's own comment) from 5 to 26 real days
# (2026-07-27..2026-08-21, yfinance's own trailing-30-day window intersected with
# START_DATE/END_DATE) -- still not a full month, but no longer the thin 5-day placeholder this
# task's data-prep phase originally shipped with.
WHISTLEBLOWER_PROVIDER: str = "yfinance"

# Precision proxy's "decisive" threshold: a flagged (bar, field) counts as yfinance deciding the
# discrepancy (not sitting as noise between the two candidates) when yfinance's deviation from the
# farther candidate exceeds this multiple of yfinance's own typical (median) deviation from the
# ibkr/massive midpoint on ordinary (non-flagged) bars, AND its deviation from the closer candidate
# is within that same typical baseline. Task's own instruction: "a configured multiple ... record
# the definition."
E6_DECISIVE_MULTIPLE: float = 3.0

# E6b (independence check): Massive-yfinance's dispersion counts as "materially tighter" than both
# ibkr-yfinance's and ibkr-massive's own dispersions (suggesting a shared upstream) when it's at
# least this many percent smaller than both.
E6B_SHARED_UPSTREAM_MARGIN_PCT: float = 30.0
