"""E2 -- adjustment mismatch (tasks/ibkr_massive_mad_calibration.md).

Per ticker, daily median of ibkr.close / massive.close at lag 0 (confirmed by E1 -- no shift
applied here). Looks for:
- jumps to a near-rational value (config.ADJUSTMENT_RATIONAL_MULTIPLES) -> split handling mismatch;
- small persistent step offsets that don't match a rational multiple -> dividend adjustment
  difference, or an unidentifiable vendor reconstruction difference.

No external corporate-actions calendar is used to *detect* this, only noted in the report to help
*explain* a flagged date, per the task's own method. IBKR method is TRADES throughout (see
config.IBKR_METHOD's comment) -- recorded prominently in the manifest since the task notes this
axis changes the expected answer (TRADES is raw/unadjusted; ADJUSTED_LAST would not be).

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/adjustment/close_ratio.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

_SHARED_PARENT = Path(__file__).resolve().parent.parent
if str(_SHARED_PARENT) not in sys.path:
    sys.path.insert(0, str(_SHARED_PARENT))

from _shared import config, load, manifest  # noqa: E402

from reconcile.cli import _EASTERN  # noqa: E402

EXPERIMENT_NAME = "adjustment"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME


def _provider_close(raw: pd.DataFrame, provider: str) -> pd.DataFrame:
    subset = raw.loc[raw["provider"] == provider, ["timestamp", "close"]].copy()
    subset["close"] = subset["close"].astype(float)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _et_date_for_index(utc_naive_index: pd.DatetimeIndex) -> pd.Series:
    et_index = utc_naive_index.tz_localize("UTC").tz_convert(_EASTERN)
    et_dates = et_index.normalize().tz_localize(None)
    return pd.Series(et_dates, index=utc_naive_index)


def _nearest_rational_multiple(ratio: float) -> tuple[float, float] | None:
    """Returns (multiple, pct_distance) for the closest configured rational multiple within
    tolerance, or None if nothing in config.ADJUSTMENT_RATIONAL_MULTIPLES is close enough."""
    best_multiple = None
    best_distance_pct = None
    for multiple in config.ADJUSTMENT_RATIONAL_MULTIPLES:
        distance_pct = abs(ratio - multiple) / multiple * 100.0
        if best_distance_pct is None or distance_pct < best_distance_pct:
            best_distance_pct = distance_pct
            best_multiple = multiple
    if best_distance_pct is not None and best_distance_pct <= config.ADJUSTMENT_JUMP_TOLERANCE_PCT:
        return best_multiple, round(best_distance_pct, 4)
    return None


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_close(raw, "ibkr").rename(columns={"close": "close_ibkr"})
    massive_df = _provider_close(raw, "massive").rename(columns={"close": "close_massive"})

    joined = ibkr_df.join(massive_df, how="inner")
    joined["ratio"] = joined["close_ibkr"] / joined["close_massive"]
    joined["et_date"] = _et_date_for_index(joined.index)

    grouped = joined.groupby("et_date")["ratio"]
    daily = grouped.median().reset_index(name="median_ratio")
    daily["n_pairs"] = grouped.size().reset_index(drop=True)
    daily = daily.sort_values("et_date").reset_index(drop=True)
    daily["ticker"] = ticker
    daily["deviation_pct"] = ((daily["median_ratio"] - 1.0) * 100.0).round(4)
    daily["is_deviating"] = daily["deviation_pct"].abs() >= config.ADJUSTMENT_MATERIALITY_PCT

    nearest_multiples = []
    nearest_distances = []
    for _, row in daily.iterrows():
        if not row["is_deviating"]:
            nearest_multiples.append(None)
            nearest_distances.append(None)
            continue
        match = _nearest_rational_multiple(row["median_ratio"])
        if match is None:
            nearest_multiples.append(None)
            nearest_distances.append(None)
        else:
            nearest_multiples.append(match[0])
            nearest_distances.append(match[1])
    daily["nearest_rational_multiple"] = nearest_multiples
    daily["rational_match_distance_pct"] = nearest_distances

    deviating_days = daily[daily["is_deviating"]]
    unidentifiable_days = deviating_days[deviating_days["nearest_rational_multiple"].isna()]

    if len(deviating_days) == 0:
        outcome = "absent"
    elif len(unidentifiable_days) == 0:
        outcome = "present_identifiable"
    else:
        outcome = "present_unidentifiable"

    overall_median_ratio = float(daily["median_ratio"].median())
    verdict = {
        "outcome": outcome,
        "overall_median_ratio": round(overall_median_ratio, 6),
        "overall_deviation_pct": round((overall_median_ratio - 1.0) * 100.0, 4),
        "n_days": int(len(daily)),
        "n_deviating_days": int(len(deviating_days)),
        "n_unidentifiable_days": int(len(unidentifiable_days)),
        "deviating_dates": [str(d.date()) for d in deviating_days["et_date"]],
        "unidentifiable_dates": [str(d.date()) for d in unidentifiable_days["et_date"]],
    }
    return daily, verdict


def _plot_ratio_series(ticker: str, daily: pd.DataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(daily["et_date"], daily["median_ratio"], marker=".", linewidth=1, label="daily median ratio")
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=1, label="1.0 (no adjustment mismatch)")
    deviating = daily[daily["is_deviating"]]
    if not deviating.empty:
        ax.scatter(deviating["et_date"], deviating["median_ratio"], color="red", zorder=5, label="deviating day")
    ax.set_title(f"{ticker}: daily median ibkr.close / massive.close")
    ax.set_xlabel("date (ET)")
    ax.set_ylabel("ratio")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_daily = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        daily, verdict = run_for_ticker(ticker)
        all_daily.append(daily)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print(f"ibkr method: {config.IBKR_METHOD} (raw/unadjusted trades -- this is the axis that changes the expected answer)")
        print(f"Days: {verdict['n_days']}, overall median ratio: {verdict['overall_median_ratio']} ({verdict['overall_deviation_pct']:+.4f}%)")
        print(f"Deviating days (>= {config.ADJUSTMENT_MATERIALITY_PCT}% from 1.0): {verdict['n_deviating_days']}")
        if verdict["deviating_dates"]:
            print(f"  dates: {verdict['deviating_dates']}")
        if verdict["unidentifiable_dates"]:
            print(f"  unidentifiable (no rational-multiple match): {verdict['unidentifiable_dates']}")

        if verdict["outcome"] == "absent":
            print("\nVerdict: ABSENT -- no adjustment mismatch detected. Proceed unchanged.")
        elif verdict["outcome"] == "present_identifiable":
            print("\nVerdict: PRESENT, IDENTIFIABLE -- every deviating day matches a rational split multiple.")
            print("Fixable with an adjustment dimension; record as a follow-up task, apply a correction in load.py for the experiment.")
        else:
            print("\nVerdict: PRESENT, UNIDENTIFIABLE -- at least one deviating day doesn't match any configured rational multiple.")
            print("Likely a vendor archive reconstruction difference. This caps how far back the band can be trusted -- feeds into E5.")

        plot_path = RESULTS_DIR / f"{ticker.lower()}_ratio_series.png"
        _plot_ratio_series(ticker, daily, plot_path)
        print(f"Wrote {plot_path}")

    daily_frame = pd.concat(all_daily, ignore_index=True)
    daily_path = RESULTS_DIR / "close_ratio_by_day.parquet"
    daily_frame.to_parquet(daily_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/adjustment/close_ratio.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "ibkr_method_pin": config.IBKR_METHOD,
            "materiality_pct": config.ADJUSTMENT_MATERIALITY_PCT,
            "jump_tolerance_pct": config.ADJUSTMENT_JUMP_TOLERANCE_PCT,
            "rational_multiples": config.ADJUSTMENT_RATIONAL_MULTIPLES,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(daily_path)],
            "verdicts": ticker_verdicts,
        },
    )

    print(f"\nWrote {daily_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
