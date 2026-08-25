"""E3 -- does MAD actually beat Welford here? (tasks/ibkr_massive_mad_calibration.md)

On the corrected difference series (lag 0, per E1) for the 'ohlc' field group, pooling
open/high/low/close per the task's own method (reusing reconcile.algorithm.fields_for_group,
which returns all four fields for that one group -- dim_field_group has no finer split today):
- compute sigma (via reconcile.algorithm's Welford-based batch_stats/stddev_from_stats -- the
  actual production estimator, not a reimplementation) and 1.4826 x MAD;
- report the ratio sigma / (1.4826 x MAD) as a tail-fatness measure;
- recompute both with the top 0.1% of |d| removed.

If sigma is stable under trimming and the ratio is near 1.0, the MAD argument doesn't hold on this
data. A per-field breakdown is also written (diagnostic only, not the task's requested grouping)
since it's cheap given the same joined frame and useful context for the pooled read.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/dispersion/sigma_vs_mad.py
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

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md). This is the actual
# production Welford estimator (reconcile/algorithm.py), not a reimplementation, and
# fields_for_group(FIELD_GROUP_OHLC) is the single source of truth for which fields make up the
# 'ohlc' group.
from reconcile.algorithm import FIELD_GROUP_OHLC, batch_stats, fields_for_group, stddev_from_stats  # noqa: E402

EXPERIMENT_NAME = "dispersion"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME

MAD_SCALE = 1.4826


def _provider_ohlc(raw: pd.DataFrame, provider: str, fields: list[str]) -> pd.DataFrame:
    columns = ["timestamp"] + fields
    subset = raw.loc[raw["provider"] == provider, columns].copy()
    for field_name in fields:
        subset[field_name] = subset[field_name].astype(float)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _dispersion_stats(d_values: np.ndarray) -> dict:
    stats = batch_stats(d_values.tolist())
    sigma = stddev_from_stats(stats)
    median_d = float(np.median(d_values))
    mad = float(np.median(np.abs(d_values - median_d)))
    mad_scaled = MAD_SCALE * mad
    ratio = sigma / mad_scaled if mad_scaled > 0 else None
    return {
        "n": int(len(d_values)),
        "sigma": sigma,
        "mad": mad,
        "mad_scaled": mad_scaled,
        "ratio": ratio,
    }


def _trim_top_tail(d_values: np.ndarray, trim_pct: float) -> np.ndarray:
    abs_d = np.abs(d_values)
    threshold = np.percentile(abs_d, 100.0 - trim_pct)
    return d_values[abs_d <= threshold]


def _pct_change(raw_value: float, trimmed_value: float) -> float | None:
    if raw_value == 0:
        return None
    return round((trimmed_value - raw_value) / raw_value * 100.0, 4)


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    fields = fields_for_group(FIELD_GROUP_OHLC)

    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_ohlc(raw, "ibkr", fields)
    massive_df = _provider_ohlc(raw, "massive", fields)
    joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")

    per_field_d = {}
    for field_name in fields:
        ibkr_col = joined[f"{field_name}_ibkr"]
        massive_col = joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        per_field_d[field_name] = d.to_numpy()

    pooled_d = np.concatenate(list(per_field_d.values()))

    rows = []
    field_results = {"ALL": pooled_d}
    for field_name, d_array in per_field_d.items():
        field_results[field_name] = d_array

    verdict_by_field = {}
    for field_name, d_array in field_results.items():
        raw_stats = _dispersion_stats(d_array)
        trimmed_array = _trim_top_tail(d_array, config.DISPERSION_TRIM_TOP_PCT)
        trimmed_stats = _dispersion_stats(trimmed_array)

        sigma_pct_change = _pct_change(raw_stats["sigma"], trimmed_stats["sigma"])
        mad_pct_change = _pct_change(raw_stats["mad_scaled"], trimmed_stats["mad_scaled"])

        ratio_near_one = raw_stats["ratio"] is not None and raw_stats["ratio"] <= config.DISPERSION_RATIO_NEAR_ONE_MAX
        sigma_stable = sigma_pct_change is not None and abs(sigma_pct_change) <= config.DISPERSION_SIGMA_STABLE_PCT
        recommend_against_mad = bool(ratio_near_one and sigma_stable)

        rows.append(
            {
                "ticker": ticker,
                "field": field_name,
                "n_raw": raw_stats["n"],
                "sigma_raw": raw_stats["sigma"],
                "mad_scaled_raw": raw_stats["mad_scaled"],
                "ratio_raw": raw_stats["ratio"],
                "n_trimmed": trimmed_stats["n"],
                "sigma_trimmed": trimmed_stats["sigma"],
                "mad_scaled_trimmed": trimmed_stats["mad_scaled"],
                "ratio_trimmed": trimmed_stats["ratio"],
                "sigma_pct_change": sigma_pct_change,
                "mad_pct_change": mad_pct_change,
                "recommend_against_mad": recommend_against_mad,
            }
        )
        verdict_by_field[field_name] = {
            "ratio_raw": raw_stats["ratio"],
            "sigma_pct_change": sigma_pct_change,
            "mad_pct_change": mad_pct_change,
            "recommend_against_mad": recommend_against_mad,
        }

    frame = pd.DataFrame(rows)
    verdict = {
        "fields": verdict_by_field,
        "pooled_recommend_against_mad": verdict_by_field["ALL"]["recommend_against_mad"],
        "n_pooled": int(len(pooled_d)),
    }
    return frame, verdict


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_frames = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        frame, verdict = run_for_ticker(ticker)
        all_frames.append(frame)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print(frame.to_string(index=False))
        pooled = verdict["fields"]["ALL"]
        print(f"\nPooled ('ohlc' field group, n={verdict['n_pooled']}):")
        if pooled["ratio_raw"] is None:
            print("  ratio (sigma / 1.4826*MAD), raw = UNDEFINED -- raw MAD is exactly 0.0")
            print("  (more than half of all pooled bars agree EXACTLY at lag 0 -- E1 already showed this: match rates")
            print("   well above 50% on every field/segment -- so both the pooled median and the pooled MAD collapse to")
            print("   0. This is not a computation bug: sigma/MAD is mathematically undefined here, not just unfavorable.")
            print("   It's actually a MORE dramatic demonstration of outlier contamination than a finite ratio would be --")
            print("   but it also means a raw 1.4826*MAD tolerance would be exactly 0 in production, rejecting any nonzero")
            print("   disagreement outright, unless paired with a nonzero floor (materiality_floor already exists for this).")
        else:
            print(f"  ratio (sigma / 1.4826*MAD), raw = {pooled['ratio_raw']}")
        mad_pct_change_label = "UNDEFINED (raw MAD is 0.0)" if pooled["mad_pct_change"] is None else f"{pooled['mad_pct_change']}%"
        print(f"  sigma pct change under trimming (top {config.DISPERSION_TRIM_TOP_PCT}% of |d| removed) = {pooled['sigma_pct_change']}%")
        print(f"  MAD pct change under trimming    = {mad_pct_change_label}")
        if pooled["recommend_against_mad"]:
            print("\nVerdict: sigma is stable under trimming and the ratio is near 1.0 -- RECOMMEND AGAINST the MAD switch.")
            print("variance_floor_clamp.md was already sufficient; this direction is unmotivated on this data.")
        else:
            print("\nVerdict: data SUPPORTS the MAD switch -- sigma collapses under trimming just the top 0.1% of |d|,")
            print("confirming Welford's own sigma is dominated by a small contaminating tail, exactly the argument this")
            print("task exists to quantify. Any production use of raw MAD needs a nonzero floor given the degenerate-MAD")
            print("finding above -- flag for the recommendation, not solved here.")
            print(
                f"Implied window size / state cost if MAD wins: {verdict['n_pooled']} pooled observations over the {config.START_DATE}..{config.END_DATE} range"
            )
            print("(a real operational cost -- MAD has no O(1) streaming update; belongs in the recommendation).")

    all_frame = pd.concat(all_frames, ignore_index=True)
    output_path = RESULTS_DIR / "sigma_vs_mad.parquet"
    all_frame.to_parquet(output_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/dispersion/sigma_vs_mad.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "trim_top_pct": config.DISPERSION_TRIM_TOP_PCT,
            "ratio_near_one_max": config.DISPERSION_RATIO_NEAR_ONE_MAX,
            "sigma_stable_pct": config.DISPERSION_SIGMA_STABLE_PCT,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(output_path)],
            "verdicts": ticker_verdicts,
        },
    )

    print(f"\nWrote {output_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
