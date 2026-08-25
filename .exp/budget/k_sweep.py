"""E4 -- k -> Databento spend (tasks/ibkr_massive_mad_calibration.md).

Sweeps k over config.E4_K_GRID. For each k, a bar is flagged if ANY of open/high/low/close has
|d| exceeding k * that field's *conditional* MAD (see below) -- mirroring
reconcile.algorithm._agrees_within_tolerance's own "every field independently within its own
tolerance" structure (OR-combined here, since we want "flagged" = "would fail at least one
field's check", the inverse of "agrees").

Flagged minutes are then clustered into contiguous ranges with a gap-merge parameter g (swept over
config.E4_G_GRID_MINUTES), reporting range count and total billed minutes alongside flag count --
Databento bills against contiguous ranges, not flag count.

Dispersion basis -- deviates from the task's literal "k * (1.4826*MAD)" on E3's raw pooled MAD,
which came out exactly 0.0 (more than half of all bars agree exactly, so the classic MAD collapses
regardless of k). Repo owner's explicit call: use "conditional MAD" instead -- computed only among
bars where d != 0 for that field, excluding the exact-match point mass. This is a real, nonzero
robust measure of how large actual disagreements run, and k sweeps meaningfully against it.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/budget/k_sweep.py
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

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md).
from reconcile.algorithm import FIELD_GROUP_OHLC, fields_for_group  # noqa: E402

EXPERIMENT_NAME = "budget"
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


def _conditional_mad_scaled(d_values: np.ndarray) -> float:
    """MAD computed only among nonzero observations -- excludes the exact-match point mass (see
    module docstring). Falls back to 0.0 if every observation happens to be zero (no disagreement
    at all -- k has nothing to sweep against for that field)."""
    nonzero = d_values[d_values != 0]
    if len(nonzero) == 0:
        return 0.0
    median_nonzero = float(np.median(nonzero))
    mad = float(np.median(np.abs(nonzero - median_nonzero)))
    return MAD_SCALE * mad


def _cluster_into_ranges(timestamps: list[pd.Timestamp], gap_minutes: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if len(timestamps) == 0:
        return []
    gap_limit = pd.Timedelta(minutes=gap_minutes)
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    range_start = timestamps[0]
    range_end = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp - range_end <= gap_limit:
            range_end = timestamp
        else:
            ranges.append((range_start, range_end))
            range_start = timestamp
            range_end = timestamp
    ranges.append((range_start, range_end))
    return ranges


def _billed_minutes(ranges: list[tuple[pd.Timestamp, pd.Timestamp]]) -> int:
    total = 0
    for range_start, range_end in ranges:
        span_minutes = int((range_end - range_start).total_seconds() // 60) + 1
        total += span_minutes
    return total


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    fields = fields_for_group(FIELD_GROUP_OHLC)

    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_ohlc(raw, "ibkr", fields)
    massive_df = _provider_ohlc(raw, "massive", fields)
    joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")

    abs_d_by_field = {}
    conditional_mad_scaled = {}
    for field_name in fields:
        ibkr_col = joined[f"{field_name}_ibkr"]
        massive_col = joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        abs_d_by_field[field_name] = d.abs()
        conditional_mad_scaled[field_name] = _conditional_mad_scaled(d.to_numpy())

    abs_d_frame = pd.DataFrame(abs_d_by_field, index=joined.index)

    budget_rows = []
    flag_counts_by_k = {}
    for k in config.E4_K_GRID:
        flagged_any = pd.Series(False, index=abs_d_frame.index)
        for field_name in fields:
            threshold = k * conditional_mad_scaled[field_name]
            flagged_any = flagged_any | (abs_d_frame[field_name] > threshold)

        flagged_timestamps = sorted(abs_d_frame.index[flagged_any])
        flag_count = len(flagged_timestamps)
        flag_counts_by_k[k] = flag_count

        for gap_minutes in config.E4_G_GRID_MINUTES:
            ranges = _cluster_into_ranges(flagged_timestamps, gap_minutes)
            range_count = len(ranges)
            billed_minutes = _billed_minutes(ranges)
            budget_rows.append(
                {
                    "ticker": ticker,
                    "k": k,
                    "g_minutes": gap_minutes,
                    "flag_count": flag_count,
                    "range_count": range_count,
                    "billed_minutes": billed_minutes,
                    "cost_usd": round(
                        billed_minutes * config.DATABENTO_OHLCV_1M_BYTES_PER_RECORD / 1e9 * config.DATABENTO_PRICE_PER_GB,
                        6,
                    ),
                }
            )

    frame = pd.DataFrame(budget_rows)
    verdict = {
        "conditional_mad_scaled": conditional_mad_scaled,
        "flag_counts_by_k": flag_counts_by_k,
        "n_bars": int(len(abs_d_frame)),
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
        print(f"n_bars (lag-0 joined minutes): {verdict['n_bars']}")
        print("Conditional MAD (scaled, fractional) per field:")
        for field_name, value in verdict["conditional_mad_scaled"].items():
            print(f"  {field_name}: {value:.8f}")

        print("\nFlag count by k (independent of g):")
        for k, count in verdict["flag_counts_by_k"].items():
            pct = round(count / verdict["n_bars"] * 100.0, 3)
            print(f"  k={k}: {count} flagged ({pct}%)")

        print("\nBilled minutes by (k, g):")
        pivot = frame.pivot_table(index="k", columns="g_minutes", values="billed_minutes")
        print(pivot.to_string())

        print("\nRange count by (k, g):")
        pivot_ranges = frame.pivot_table(index="k", columns="g_minutes", values="range_count")
        print(pivot_ranges.to_string())

        print(
            f"\nEstimated Databento cost (USD) by (k, g) -- OHLCV-1m, "
            f"{config.DATABENTO_OHLCV_1M_BYTES_PER_RECORD} bytes/record, ${config.DATABENTO_PRICE_PER_GB}/GB:"
        )
        pivot_cost = frame.pivot_table(index="k", columns="g_minutes", values="cost_usd")
        print(pivot_cost.to_string())
        print(f"\nFull grid range: ${frame['cost_usd'].min():.6f} .. ${frame['cost_usd'].max():.6f}")

    all_frame = pd.concat(all_frames, ignore_index=True)
    output_path = RESULTS_DIR / "k_g_budget.parquet"
    all_frame.to_parquet(output_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/budget/k_sweep.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "k_grid": config.E4_K_GRID,
            "g_grid_minutes": config.E4_G_GRID_MINUTES,
            "dispersion_basis": "conditional_mad (repo owner's explicit call, see script docstring -- raw pooled MAD from E3 was exactly 0.0)",
            "databento_schema": "OHLCV-1m",
            "databento_bytes_per_record": config.DATABENTO_OHLCV_1M_BYTES_PER_RECORD,
            "databento_price_per_gb_usd": config.DATABENTO_PRICE_PER_GB,
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
