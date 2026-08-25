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
# candidate rows stay unpurged), confirmed live 2026-08-25: ibkr 2025-12-31..2026-07-31 (140,160
# rows), massive 2026-01-02..2026-07-31 (133,146 rows). Intentionally asymmetric at the start --
# ibkr's extra day is exactly the kind of gap E0 exists to surface, not an input error to correct.
# Deliberately excludes a handful of stray rows dated 2026-08-03/2026-08-10 (1-2 rows per provider
# each) -- confirmed these are ordinary same-day pipeline activity outside the frozen window
# (the big backfill's own reconcile job is disabled, but small routine jobs for recent dates keep
# running), not part of the dataset this task is measuring.
START_DATE: date = date(2025, 12, 31)
END_DATE: date = date(2026, 7, 31)

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
