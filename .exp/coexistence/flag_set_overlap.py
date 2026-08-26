"""E8 -- replace or coexist (tasks/ibkr_massive_mad_calibration.md).

On the overlap window, at the recommended k (config.E8_RECOMMENDED_K, from E6's own gate),
compares the flag set from the ibkr/massive MAD band against the flag set from the existing
ibkr/yfinance Welford band (reusing reconcile.algorithm._agrees_within_tolerance directly, same
as E6's recall proxy -- not a reimplementation). Reports the confusion matrix (both / MAD-only /
whistleblower-only / neither) and a verdict: MAD-superset (recommend MAD as primary for the
pre-overlap historical period) or substantially disjoint (coexistence earned).

Scope of "replace": decides which band is the primary whistleblower for the pre-overlap historical
period ONLY. Does not decide whether yfinance stays in the pipeline at all -- that's settled by
the task's own structural limitation (a two-provider band can't detect correlated error) and is
not on the table here.

CIRCULARITY WARNING (task's own instruction, printed prominently below): E6 and E8 are both
computed *using* yfinance. A "superset" result would be derived from the very source it would
appear to justify de-emphasizing, and it would hold for one window, one alignment regime, one k.
Treat any superset finding as a claim about band coverage, not about yfinance's necessity.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/coexistence/flag_set_overlap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SHARED_PARENT = Path(__file__).resolve().parent.parent
if str(_SHARED_PARENT) not in sys.path:
    sys.path.insert(0, str(_SHARED_PARENT))

from _shared import config, load, manifest  # noqa: E402

from quant_data._internal.shared.settings import DEFAULT_RECONCILE_K  # noqa: E402

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md).
from reconcile.algorithm import (  # noqa: E402
    DATA_QUALITY_ACCEPTED,
    FIELD_GROUP_OHLC,
    ROLE_CANDIDATE,
    ROLE_WHISTLEBLOWER,
    FieldTolerance,
    ProviderBar,
    _agrees_within_tolerance,
    fields_for_group,
)

EXPERIMENT_NAME = "coexistence"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME

MAD_SCALE = 1.4826


def _provider_frame(raw: pd.DataFrame, provider: str, fields: list[str], include_quality: bool) -> pd.DataFrame:
    columns = ["timestamp"] + fields
    if include_quality:
        columns = columns + ["data_quality"]
    subset = raw.loc[raw["provider"] == provider, columns].copy()
    for field_name in fields:
        subset[field_name] = subset[field_name].astype(float)
        if provider == config.WHISTLEBLOWER_PROVIDER:
            # Same float32-storage-artifact fix as E6 -- see that script's docstring/comment for
            # the full explanation (confirmed live: yfinance stores e.g. 737.239990234375 instead
            # of 737.24). Round to cent precision before any comparison.
            subset[field_name] = subset[field_name].round(2)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _conditional_mad_scaled(d_values: np.ndarray) -> float:
    nonzero = d_values[d_values != 0]
    if len(nonzero) == 0:
        return 0.0
    median_nonzero = float(np.median(nonzero))
    mad = float(np.median(np.abs(nonzero - median_nonzero)))
    return MAD_SCALE * mad


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    fields = fields_for_group(FIELD_GROUP_OHLC)
    providers = config.CANDIDATE_PROVIDERS + (config.WHISTLEBLOWER_PROVIDER,)

    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, providers, config.START_DATE, config.END_DATE)
        whistleblower_stddev = load.fetch_provider_pair_disagreement_stddev(connection, ticker, "ibkr")

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_frame(raw, "ibkr", fields, include_quality=False)
    massive_df = _provider_frame(raw, "massive", fields, include_quality=False)
    yfinance_df = _provider_frame(raw, config.WHISTLEBLOWER_PROVIDER, fields, include_quality=True)
    yfinance_accepted = yfinance_df[yfinance_df["data_quality"] == DATA_QUALITY_ACCEPTED]

    # Full-range conditional MAD basis -- same computation, same data range as E4/E6, so the band
    # being compared here is the identical band those experiments already characterized.
    im_joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")
    conditional_mad_scaled = {}
    for field_name in fields:
        ibkr_col = im_joined[f"{field_name}_ibkr"]
        massive_col = im_joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        conditional_mad_scaled[field_name] = _conditional_mad_scaled(d.to_numpy())

    yf_rename_map = {}
    for field_name in fields:
        yf_rename_map[field_name] = f"{field_name}_yf"
    yfinance_renamed = yfinance_accepted[fields].rename(columns=yf_rename_map)
    triple = im_joined.join(yfinance_renamed, how="inner").copy()

    if len(triple) == 0:
        raise RuntimeError(f"No triple-overlap bars found for ticker={ticker!r}.")

    overlap_start = triple.index.min()
    overlap_end = triple.index.max()

    # MAD-band flag, at the recommended k, bar-level (any field triggers).
    mad_flagged_any = pd.Series(False, index=triple.index)
    for field_name in fields:
        ibkr_col = triple[f"{field_name}_ibkr"]
        massive_col = triple[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        threshold = config.E8_RECOMMENDED_K * conditional_mad_scaled[field_name]
        mad_flagged_any = mad_flagged_any | (d.abs() > threshold)
    triple["mad_flagged"] = mad_flagged_any

    # Existing whistleblower flag -- real production tolerance, not a redefinition.
    field_tolerances = {}
    for field_name in fields:
        stddev = whistleblower_stddev.get(field_name, 0.0)
        field_tolerances[field_name] = FieldTolerance(stddev=stddev, floor_value=0.0)

    whistleblower_flags = []
    for _, row in triple.iterrows():
        candidate_bar = ProviderBar(
            provider_id=0,
            provider_name="ibkr",
            role=ROLE_CANDIDATE,
            open=row["open_ibkr"],
            high=row["high_ibkr"],
            low=row["low_ibkr"],
            close=row["close_ibkr"],
            volume=0.0,
            data_quality=DATA_QUALITY_ACCEPTED,
        )
        whistleblower_bar = ProviderBar(
            provider_id=0,
            provider_name=config.WHISTLEBLOWER_PROVIDER,
            role=ROLE_WHISTLEBLOWER,
            open=row["open_yf"],
            high=row["high_yf"],
            low=row["low_yf"],
            close=row["close_yf"],
            volume=0.0,
            data_quality=DATA_QUALITY_ACCEPTED,
        )
        agrees = _agrees_within_tolerance(candidate_bar, whistleblower_bar, FIELD_GROUP_OHLC, field_tolerances, DEFAULT_RECONCILE_K)
        whistleblower_flags.append(not agrees)
    triple["whistleblower_flagged"] = whistleblower_flags

    both = int((triple["mad_flagged"] & triple["whistleblower_flagged"]).sum())
    mad_only = int((triple["mad_flagged"] & ~triple["whistleblower_flagged"]).sum())
    whistleblower_only = int((~triple["mad_flagged"] & triple["whistleblower_flagged"]).sum())
    neither = int((~triple["mad_flagged"] & ~triple["whistleblower_flagged"]).sum())

    n_mad_total = both + mad_only
    n_whistleblower_total = both + whistleblower_only
    union = both + mad_only + whistleblower_only
    jaccard = round(both / union, 4) if union > 0 else None
    miss_pct = round(whistleblower_only / n_whistleblower_total * 100.0, 3) if n_whistleblower_total > 0 else None

    is_superset = miss_pct is not None and miss_pct <= config.E8_SUPERSET_MISS_MAX_PCT

    confusion = pd.DataFrame(
        [
            {"ticker": ticker, "category": "both", "n_bars": both},
            {"ticker": ticker, "category": "mad_only", "n_bars": mad_only},
            {"ticker": ticker, "category": "whistleblower_only", "n_bars": whistleblower_only},
            {"ticker": ticker, "category": "neither", "n_bars": neither},
        ]
    )

    verdict = {
        "overlap_start": str(overlap_start.date()),
        "overlap_end": str(overlap_end.date()),
        "n_overlap_bars": int(len(triple)),
        "k": config.E8_RECOMMENDED_K,
        "both": both,
        "mad_only": mad_only,
        "whistleblower_only": whistleblower_only,
        "neither": neither,
        "n_mad_total": n_mad_total,
        "n_whistleblower_total": n_whistleblower_total,
        "jaccard": jaccard,
        "whistleblower_miss_pct": miss_pct,
        "is_superset": bool(is_superset),
    }
    return confusion, verdict


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_confusion = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        confusion, verdict = run_for_ticker(ticker)
        all_confusion.append(confusion)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print("*** CIRCULARITY WARNING: this comparison is computed USING yfinance -- a 'superset' result")
        print("    is derived from the very source it would appear to justify de-emphasizing, and holds for")
        print("    one window, one alignment regime, one k. A claim about band COVERAGE, not necessity. ***")
        print(f"Overlap window: {verdict['overlap_start']} .. {verdict['overlap_end']} ({verdict['n_overlap_bars']} bars), k={verdict['k']}")

        print("\nConfusion matrix:")
        print(f"  both flagged:              {verdict['both']}")
        print(f"  MAD only:                  {verdict['mad_only']}")
        print(f"  whistleblower only:        {verdict['whistleblower_only']}")
        print(f"  neither:                   {verdict['neither']}")
        print(f"  MAD band total flagged:    {verdict['n_mad_total']}")
        print(f"  whistleblower total flagged: {verdict['n_whistleblower_total']}")
        print(f"  Jaccard (both / union):    {verdict['jaccard']}")
        print(f"  whistleblower bars MAD misses: {verdict['whistleblower_miss_pct']}% (superset threshold <= {config.E8_SUPERSET_MISS_MAX_PCT}%)")

        if verdict["is_superset"]:
            print("\nVerdict: MAD FLAGS ARE A SUPERSET -- recommend the MAD band as primary for the historical period.")
            print("materiality_floor_tolerance.md / variance_floor_clamp.md may become retirable for that period --")
            print("flag for review, do not assume.")
        else:
            print("\nVerdict: SUBSTANTIALLY DISJOINT -- the two bands detect different failure modes.")
            print("Coexistence is earned. Recommend it explicitly: MAD band owns the pre-overlap historical")
            print("period (no yfinance available there at all); Welford/yfinance band keeps owning the")
            print("recent/rolling period it already covers.")

    confusion_frame = pd.concat(all_confusion, ignore_index=True)
    confusion_path = RESULTS_DIR / "flag_set_overlap.parquet"
    confusion_frame.to_parquet(confusion_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/coexistence/flag_set_overlap.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "whistleblower_provider": config.WHISTLEBLOWER_PROVIDER,
            "recommended_k": config.E8_RECOMMENDED_K,
            "superset_miss_max_pct": config.E8_SUPERSET_MISS_MAX_PCT,
            "production_k_used_for_whistleblower": DEFAULT_RECONCILE_K,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(confusion_path)],
            "verdicts": ticker_verdicts,
        },
    )

    print(f"\nWrote {confusion_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
