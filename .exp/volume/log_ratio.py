"""E7 -- volume (tasks/ibkr_massive_mad_calibration.md).

Own field group -- not part of 'ohlc'. Distribution of log(massive.volume / ibkr.volume) at lag 0
(confirmed by E1), by session segment. IBKR TRADES is not the consolidated tape, so systematic
disagreement with Massive's own aggregate volume is expected; forcing it into the same band as
price would manufacture flags forever.

Gate: stable center + modest dispersion across segments -> recommend a separate k for a
log-ratio-based volume band. Unstable -> recommend excluding volume from reconciliation entirely.
"Stable"/"modest" thresholds are config.py-recorded judgment calls (no natural materiality scale
for a volume-count ratio the way there was for a price relative-difference) -- printed prominently,
not assumed obvious.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/volume/log_ratio.py
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
from reconcile.algorithm import batch_stats, stddev_from_stats  # noqa: E402
from reconcile.cli import _session_segment  # noqa: E402

EXPERIMENT_NAME = "volume"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME

MAD_SCALE = 1.4826
_SEGMENT_LABELS = {0: "pre", 1: "rth", 2: "post"}


def _provider_volume(raw: pd.DataFrame, provider: str) -> pd.DataFrame:
    subset = raw.loc[raw["provider"] == provider, ["timestamp", "volume"]].copy()
    subset["volume"] = subset["volume"].astype(float)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _segment_for_index(utc_naive_index: pd.DatetimeIndex) -> pd.Series:
    segments = []
    for timestamp in utc_naive_index:
        segments.append(_SEGMENT_LABELS[_session_segment(timestamp)])
    return pd.Series(segments, index=utc_naive_index)


def _mad_scaled(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    median_value = float(np.median(values))
    mad = float(np.median(np.abs(values - median_value)))
    return MAD_SCALE * mad


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict]:
    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_volume(raw, "ibkr").rename(columns={"volume": "volume_ibkr"})
    massive_df = _provider_volume(raw, "massive").rename(columns={"volume": "volume_massive"})
    joined = ibkr_df.join(massive_df, how="inner")
    joined["segment"] = _segment_for_index(joined.index)

    n_joined = len(joined)
    nonzero = joined[(joined["volume_ibkr"] > 0) & (joined["volume_massive"] > 0)].copy()
    n_zero_excluded = n_joined - len(nonzero)
    nonzero["log_ratio"] = np.log(nonzero["volume_massive"] / nonzero["volume_ibkr"])

    segment_stats = {}
    for segment_name in ["pre", "rth", "post"]:
        segment_values = nonzero.loc[nonzero["segment"] == segment_name, "log_ratio"].to_numpy()
        segment_stats[segment_name] = {
            "n": int(len(segment_values)),
            "median": float(np.median(segment_values)) if len(segment_values) > 0 else None,
            "mad_scaled": _mad_scaled(segment_values) if len(segment_values) > 0 else None,
        }

    centers = []
    dispersions = []
    for stats in segment_stats.values():
        if stats["median"] is not None:
            centers.append(stats["median"])
        if stats["mad_scaled"] is not None:
            dispersions.append(stats["mad_scaled"])

    max_center_spread = 0.0
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            spread = abs(centers[i] - centers[j])
            if spread > max_center_spread:
                max_center_spread = spread
    max_dispersion = max(dispersions) if dispersions else 0.0

    is_stable = max_center_spread <= config.E7_STABLE_CENTER_SPREAD_MAX
    is_modest = max_dispersion <= config.E7_MODEST_DISPERSION_MAX
    gate_passes = is_stable and is_modest

    pooled_values = nonzero["log_ratio"].to_numpy()
    pooled_center = float(np.median(pooled_values))
    pooled_dispersion = _mad_scaled(pooled_values)

    # Same tail-fatness diagnostic as E3 (sigma / (1.4826*MAD)) -- "modest" MAD doesn't by itself
    # rule out a heavy tail; this makes the shape explicit rather than letting the MAD-only gate
    # imply more than it actually checks.
    pooled_stats = batch_stats(pooled_values.tolist())
    pooled_sigma = stddev_from_stats(pooled_stats)
    tail_fatness_ratio = round(pooled_sigma / pooled_dispersion, 4) if pooled_dispersion > 0 else None

    recommended_k = None
    recommended_flag_rate = None
    k_sweep_rows = []
    exact_k_for_target = None
    if gate_passes and pooled_dispersion > 0:
        best_k = None
        best_distance = None
        for k in config.E7_K_GRID:
            threshold = k * pooled_dispersion
            flagged = np.abs(pooled_values - pooled_center) > threshold
            flag_rate_pct = float(flagged.mean() * 100.0)
            k_sweep_rows.append({"k": k, "flag_rate_pct": round(flag_rate_pct, 4)})
            distance = abs(flag_rate_pct - config.E7_TARGET_FLAG_RATE_PCT)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_k = k
                recommended_flag_rate = round(flag_rate_pct, 4)
        recommended_k = best_k

        # Directly computed k that would actually hit the target flag rate exactly, via the
        # empirical percentile of |value-center|/dispersion -- the grid above is coarse and tops
        # out at E7_K_GRID's own max, which can silently mask a much heavier tail than a
        # discrete sweep would reveal.
        scaled_abs_dev = np.abs(pooled_values - pooled_center) / pooled_dispersion
        exact_k_for_target = float(round(np.percentile(scaled_abs_dev, 100.0 - config.E7_TARGET_FLAG_RATE_PCT), 4))

    summary_rows = []
    for segment_name, stats in segment_stats.items():
        summary_rows.append(
            {
                "ticker": ticker,
                "segment": segment_name,
                "n": stats["n"],
                "median_log_ratio": stats["median"],
                "mad_scaled": stats["mad_scaled"],
            }
        )
    summary = pd.DataFrame(summary_rows)

    verdict = {
        "n_joined": n_joined,
        "n_zero_excluded": n_zero_excluded,
        "max_center_spread": round(max_center_spread, 4),
        "max_dispersion": round(max_dispersion, 4),
        "is_stable": bool(is_stable),
        "is_modest": bool(is_modest),
        "gate_passes": bool(gate_passes),
        "pooled_center": round(pooled_center, 4),
        "pooled_dispersion": round(pooled_dispersion, 4),
        "pooled_sigma": round(pooled_sigma, 4),
        "tail_fatness_ratio": tail_fatness_ratio,
        "recommended_k_from_grid": recommended_k,
        "recommended_flag_rate_pct": recommended_flag_rate,
        "exact_k_for_target": exact_k_for_target,
        "k_sweep": k_sweep_rows,
    }
    return summary, verdict, nonzero


def _plot_boxplot(ticker: str, nonzero: pd.DataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    data = []
    labels = []
    for segment_name in ["pre", "rth", "post"]:
        values = nonzero.loc[nonzero["segment"] == segment_name, "log_ratio"]
        if len(values) > 0:
            data.append(values.to_numpy())
            labels.append(segment_name)
    ax.boxplot(data, tick_labels=labels, showfliers=False)
    ax.axhline(0.0, color="gray", linestyle="--", linewidth=1, label="0 (equal volume)")
    ax.set_title(f"{ticker}: log(massive.volume / ibkr.volume) by session segment")
    ax.set_ylabel("log ratio")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_summaries = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        summary, verdict, nonzero = run_for_ticker(ticker)
        all_summaries.append(summary)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print(f"Joined bars: {verdict['n_joined']}, excluded (zero volume on either side): {verdict['n_zero_excluded']}")
        print("\nPer-segment median (center) and MAD-scaled dispersion of log(massive/ibkr):")
        print(summary.to_string(index=False))

        stable_label = "STABLE" if verdict["is_stable"] else "UNSTABLE"
        print(f"\nStability check: max pairwise center spread = {verdict['max_center_spread']}")
        print(f"  (threshold <= {config.E7_STABLE_CENTER_SPREAD_MAX}) -> {stable_label}")
        modest_label = "MODEST" if verdict["is_modest"] else "NOT MODEST"
        print(f"Modesty check: max segment dispersion = {verdict['max_dispersion']}")
        print(f"  (threshold <= {config.E7_MODEST_DISPERSION_MAX}) -> {modest_label}")

        if verdict["gate_passes"]:
            print("\nVerdict: STABLE center + MODEST dispersion -- recommend a separate k on the log-ratio.")
            print(f"Pooled (all segments) center={verdict['pooled_center']}, MAD-scaled dispersion={verdict['pooled_dispersion']}")
            print(f"  sigma={verdict['pooled_sigma']}")
            print(f"Tail-fatness ratio (sigma / (1.4826*MAD), same diagnostic as E3) = {verdict['tail_fatness_ratio']}")
            print("-- a 'modest' MAD does not by itself rule out a heavy tail.")
            print("k sweep (flag rate %, target is a rough match to E4's own k=3 price-band operating point):")
            for row in verdict["k_sweep"]:
                marker = "  <-- closest in grid" if row["k"] == verdict["recommended_k_from_grid"] else ""
                print(f"  k={row['k']}: {row['flag_rate_pct']}%{marker}")
            print(f"Closest grid k = {verdict['recommended_k_from_grid']} (flag rate {verdict['recommended_flag_rate_pct']}%,")
            print(f"  target {config.E7_TARGET_FLAG_RATE_PCT}%)")
            print(f"Exact k for the {config.E7_TARGET_FLAG_RATE_PCT}% target (empirical percentile, not grid-limited)")
            print(f"  = {verdict['exact_k_for_target']}")
            print(f"Recommended k_volume = {verdict['exact_k_for_target']}")
        else:
            print("\nVerdict: UNSTABLE and/or dispersion not modest -- RECOMMEND EXCLUDING VOLUME FROM RECONCILIATION ENTIRELY.")
            print("A single global log-ratio band would not fit all session segments; volume is not a good")
            print("candidate for MAD-band tolerance treatment on this evidence.")

        plot_path = RESULTS_DIR / f"{ticker.lower()}_log_ratio_boxplot.png"
        _plot_boxplot(ticker, nonzero, plot_path)
        print(f"Wrote {plot_path}")

    summary_frame = pd.concat(all_summaries, ignore_index=True)
    summary_path = RESULTS_DIR / "log_ratio_summary.parquet"
    summary_frame.to_parquet(summary_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/volume/log_ratio.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "stable_center_spread_max": config.E7_STABLE_CENTER_SPREAD_MAX,
            "modest_dispersion_max": config.E7_MODEST_DISPERSION_MAX,
            "k_grid": config.E7_K_GRID,
            "target_flag_rate_pct": config.E7_TARGET_FLAG_RATE_PCT,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(summary_path)],
            "verdicts": ticker_verdicts,
        },
    )

    print(f"\nWrote {summary_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
