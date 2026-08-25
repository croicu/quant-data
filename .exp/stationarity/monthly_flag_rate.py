"""E5 -- stationarity (tasks/ibkr_massive_mad_calibration.md), the task's own "highest-value
experiment".

Calibrates the band (conditional MAD per field, same basis as E4 -- repo owner's explicit call)
on the most recent calendar month (ET) in the configured range, fixes k at
config.E5_K_FIXED (production's own default, the same anchor E3/E4 already reported against), then
applies that *frozen* per-field threshold unchanged to every earlier month and measures flag rate
per month. A band that only works on the month it was calibrated on isn't stationary, and the
whole historical-backfill plan rests on it being roughly so.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/stationarity/monthly_flag_rate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_SHARED_PARENT = Path(__file__).resolve().parent.parent
if str(_SHARED_PARENT) not in sys.path:
    sys.path.insert(0, str(_SHARED_PARENT))

from _shared import config, load, manifest  # noqa: E402

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md).
from reconcile.algorithm import FIELD_GROUP_OHLC, fields_for_group  # noqa: E402
from reconcile.cli import _EASTERN  # noqa: E402

EXPERIMENT_NAME = "stationarity"
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
    nonzero = d_values[d_values != 0]
    if len(nonzero) == 0:
        return 0.0
    median_nonzero = float(np.median(nonzero))
    mad = float(np.median(np.abs(nonzero - median_nonzero)))
    return MAD_SCALE * mad


def _et_month_for_index(utc_naive_index: pd.DatetimeIndex) -> pd.Series:
    et_index = utc_naive_index.tz_localize("UTC").tz_convert(_EASTERN)
    months = et_index.tz_localize(None).to_period("M").astype(str)
    return pd.Series(months, index=utc_naive_index)


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    fields = fields_for_group(FIELD_GROUP_OHLC)

    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_ohlc(raw, "ibkr", fields)
    massive_df = _provider_ohlc(raw, "massive", fields)
    joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")
    joined["month"] = _et_month_for_index(joined.index)

    abs_d_by_field = {}
    for field_name in fields:
        ibkr_col = joined[f"{field_name}_ibkr"]
        massive_col = joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        abs_d_by_field[field_name] = d.abs()
    abs_d_frame = pd.DataFrame(abs_d_by_field, index=joined.index)
    abs_d_frame["month"] = joined["month"]

    months_sorted = sorted(abs_d_frame["month"].unique())
    calibration_month = months_sorted[-1]

    calibration_rows = joined[joined["month"] == calibration_month]
    conditional_mad_scaled = {}
    for field_name in fields:
        ibkr_col = calibration_rows[f"{field_name}_ibkr"]
        massive_col = calibration_rows[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        conditional_mad_scaled[field_name] = _conditional_mad_scaled(d.to_numpy())

    thresholds = {}
    for field_name in fields:
        thresholds[field_name] = config.E5_K_FIXED * conditional_mad_scaled[field_name]

    flagged_any = pd.Series(False, index=abs_d_frame.index)
    for field_name in fields:
        flagged_any = flagged_any | (abs_d_frame[field_name] > thresholds[field_name])
    abs_d_frame["flagged"] = flagged_any

    monthly_rows = []
    for month in months_sorted:
        month_rows = abs_d_frame[abs_d_frame["month"] == month]
        n_bars = int(len(month_rows))
        n_flagged = int(month_rows["flagged"].sum())
        flag_rate_pct = round(n_flagged / n_bars * 100.0, 4) if n_bars > 0 else None
        monthly_rows.append(
            {
                "ticker": ticker,
                "month": month,
                "is_calibration_month": month == calibration_month,
                "n_bars": n_bars,
                "n_flagged": n_flagged,
                "flag_rate_pct": flag_rate_pct,
                "low_sample": n_bars < config.E5_MIN_BARS_FOR_FULL_CONFIDENCE,
            }
        )
    monthly = pd.DataFrame(monthly_rows)

    full_confidence = monthly[~monthly["low_sample"]].sort_values("month")
    trend_direction = "flat"
    max_rate = None
    min_rate = None
    if len(full_confidence) >= 2:
        rates = full_confidence["flag_rate_pct"].to_numpy()
        max_rate = float(rates.max())
        min_rate = float(rates.min())
        calibration_rate = float(full_confidence.loc[full_confidence["is_calibration_month"], "flag_rate_pct"].iloc[0])
        earliest_rate = float(full_confidence.iloc[0]["flag_rate_pct"])
        if earliest_rate > calibration_rate * 1.5:
            trend_direction = "rising_going_back"
        elif max_rate > 0 and (max_rate - min_rate) / max_rate > 0.5:
            trend_direction = "uneven"
        else:
            trend_direction = "flat"

    verdict = {
        "calibration_month": calibration_month,
        "k_fixed": config.E5_K_FIXED,
        "conditional_mad_scaled": conditional_mad_scaled,
        "trend_direction": trend_direction,
        "min_flag_rate_pct": min_rate,
        "max_flag_rate_pct": max_rate,
        "n_months": int(len(monthly)),
        "n_low_sample_months": int(monthly["low_sample"].sum()),
    }
    return monthly, verdict


def _plot_monthly_flag_rate(ticker: str, monthly: pd.DataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4))
    ordered = monthly.sort_values("month")
    colors = []
    for _, row in ordered.iterrows():
        if row["is_calibration_month"]:
            colors.append("red")
        elif row["low_sample"]:
            colors.append("orange")
        else:
            colors.append("tab:blue")
    ax.plot(ordered["month"], ordered["flag_rate_pct"], color="gray", linewidth=1, zorder=1)
    ax.scatter(ordered["month"], ordered["flag_rate_pct"], c=colors, zorder=2)
    ax.set_title(f"{ticker}: flag rate by month (band calibrated on month in red; orange = low sample)")
    ax.set_xlabel("month (ET)")
    ax.set_ylabel("flag rate (%)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_monthly = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        monthly, verdict = run_for_ticker(ticker)
        all_monthly.append(monthly)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print(f"Calibration month: {verdict['calibration_month']} (k fixed at {verdict['k_fixed']})")
        print("Conditional MAD (scaled, fractional) per field, calibration month only:")
        for field_name, value in verdict["conditional_mad_scaled"].items():
            print(f"  {field_name}: {value:.8f}")
        print(
            f"\n{verdict['n_low_sample_months']} of {verdict['n_months']} months below the {config.E5_MIN_BARS_FOR_FULL_CONFIDENCE}-bar low-sample threshold."
        )
        print("\nFlag rate by month:")
        print(monthly.sort_values("month").to_string(index=False))

        print(f"\nVerdict (excluding low-sample months): {verdict['trend_direction']}")
        if verdict["trend_direction"] == "flat":
            print("FLAT -- one global band works. Best case; no time-varying band or trust cutoff needed.")
        elif verdict["trend_direction"] == "rising_going_back":
            print("RISING going back -- earliest full-confidence month's flag rate is >1.5x the calibration month's.")
            print("This changes backfill scope: needs a time-varying band or a hard trust cutoff date.")
            print("Cross-reference against E2's step dates before concluding it's a reconstruction difference")
            print("rather than an unhandled corporate action -- E2 found zero step dates for SPY, so if this")
            print("fires, it is NOT explained by a split/dividend mismatch already ruled out there.")
        else:
            print("UNEVEN, but NOT rising going back -- more than 2x spread between the lowest and highest")
            print("full-confidence month's flag rate, yet the earliest month is not the worst one (no systematic")
            print("temporal-drift pattern). Good news for stationarity: no evidence the band degrades further back")
            print("in history. The outlier month(s) don't fit a trend story -- cross-reference against E2's step")
            print("dates (SPY: zero found) and inspect the plot before writing off the spike as pure noise.")

        plot_path = RESULTS_DIR / f"{ticker.lower()}_flag_rate_by_month.png"
        _plot_monthly_flag_rate(ticker, monthly, plot_path)
        print(f"Wrote {plot_path}")

    all_frame = pd.concat(all_monthly, ignore_index=True)
    output_path = RESULTS_DIR / "monthly_flag_rate.parquet"
    all_frame.to_parquet(output_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/stationarity/monthly_flag_rate.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "k_fixed": config.E5_K_FIXED,
            "dispersion_basis": "conditional_mad, calibrated on the most recent month only (see E4's status note for why conditional, not raw, MAD)",
            "min_bars_for_full_confidence": config.E5_MIN_BARS_FOR_FULL_CONFIDENCE,
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
