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
