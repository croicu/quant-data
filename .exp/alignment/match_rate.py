"""E1 -- timestamp alignment (tasks/ibkr_massive_mad_calibration.md).

Exact-match rate on `high`/`low` between ibkr(t) and massive(t + lag), for
lag in config.ALIGNMENT_LAGS_MINUTES, aggregated by session segment and DST regime. Correct
alignment should show a step-function spike at one lag, not a gentle optimum -- if the winning lag
is nonzero, or differs across segment/regime, the join in load.py needs a correction that every
later experiment must inherit.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/. Independent of E0 --
re-fetches raw staging rows directly rather than reading E0's output, since E0's coverage
classification carries no OHLC values and was computed at a fixed lag of 0 only.

Run from the repo root: python .exp/alignment/match_rate.py
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
from reconcile.cli import _EASTERN, _session_segment  # noqa: E402

EXPERIMENT_NAME = "alignment"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME

# A winning lag is reported as a "clear spike" (vs. a suspicious near-tie -- the task's own
# escalation trigger) when its score beats the runner-up lag by at least this many percentage
# points. Not a pass/fail gate -- E1's own gate is a judgment call ("If no lag shows a clear
# spike, escalate"), so this only flags the read for the printed report/manifest, it does not
# change the script's exit code.
CLEAR_SPIKE_MARGIN_PCT = 10.0

# Ad-hoc addendum to E1, not part of the task file's original method: tests whether ibkr volume
# correlates with high/low disagreement at the confirmed-correct lag (0), the working theory for
# why RTH shows *lower* agreement than pre/post despite being the most liquid segment. Same
# methodology (log-log regression among disagreeing bars, R^2 reported) as the materiality_floor
# calibration (croicu/quant-data#40) on the unrelated ibkr/yfinance pair, for direct comparability.
MIN_MISMATCHED_BARS_FOR_REGRESSION = 10

_OFFSET_TO_REGIME = {"-0400": "EDT", "-0500": "EST"}
_SEGMENT_LABELS = {0: "pre", 1: "rth", 2: "post"}


def _dst_regime_for_index(utc_naive_index: pd.DatetimeIndex) -> pd.Series:
    et_index = utc_naive_index.tz_localize("UTC").tz_convert(_EASTERN)
    offset_strings = et_index.strftime("%z")
    regimes = []
    for offset_string in offset_strings:
        regimes.append(_OFFSET_TO_REGIME.get(offset_string, "unknown"))
    return pd.Series(regimes, index=utc_naive_index)


def _segment_for_index(utc_naive_index: pd.DatetimeIndex) -> pd.Series:
    segments = []
    for timestamp in utc_naive_index:
        segments.append(_SEGMENT_LABELS[_session_segment(timestamp)])
    return pd.Series(segments, index=utc_naive_index)


def _provider_frame(raw: pd.DataFrame, provider: str) -> pd.DataFrame:
    subset = raw.loc[raw["provider"] == provider, ["timestamp", "high", "low", "volume"]].copy()
    # psycopg returns NUMERIC columns as decimal.Decimal -- cast to float here (not upstream in
    # load.py, which E0 never needed to touch since it only checked row presence) so arithmetic
    # on high/low/volume below doesn't choke on Decimal/float mixing.
    subset["high"] = subset["high"].astype(float)
    subset["low"] = subset["low"].astype(float)
    subset["volume"] = subset["volume"].astype(float)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _volume_correlation_analysis(joined_lag0: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Does ibkr volume predict high/low disagreement at lag 0? Two angles per field: (1) is the
    median ibkr volume higher on mismatched bars than matched bars (RTH vs overall), and (2) among
    only the mismatched bars, does a log-log regression of ibkr volume against disagreement
    magnitude show a real relationship (slope, R^2) -- same shape of test as the materiality_floor
    calibration's own ibkr-volume-vs-disagreement finding on the ibkr/yfinance pair."""
    df = joined_lag0.copy()
    df["reference_high"] = (df["high_ibkr"] + df["high_massive"]) / 2.0
    df["reference_low"] = (df["low_ibkr"] + df["low_massive"]) / 2.0
    df["abs_diff_high"] = (df["high_ibkr"] - df["high_massive"]).abs() / df["reference_high"]
    df["abs_diff_low"] = (df["low_ibkr"] - df["low_massive"]).abs() / df["reference_low"]

    rth_df = df[df["segment"] == "rth"]

    stats = {}
    for field in ["high", "low"]:
        match_col = f"{field}_match"
        diff_col = f"abs_diff_{field}"

        matched_volume = df.loc[df[match_col], "volume_ibkr"]
        mismatched_volume = df.loc[~df[match_col], "volume_ibkr"]
        rth_matched_volume = rth_df.loc[rth_df[match_col], "volume_ibkr"]
        rth_mismatched_volume = rth_df.loc[~rth_df[match_col], "volume_ibkr"]

        field_stats = {
            "matched_median_volume": _safe_median(matched_volume),
            "mismatched_median_volume": _safe_median(mismatched_volume),
            "rth_matched_median_volume": _safe_median(rth_matched_volume),
            "rth_mismatched_median_volume": _safe_median(rth_mismatched_volume),
        }

        mismatched = df.loc[~df[match_col] & (df[diff_col] > 0) & (df["volume_ibkr"] > 0)]
        if len(mismatched) >= MIN_MISMATCHED_BARS_FOR_REGRESSION:
            log_volume = np.log(mismatched["volume_ibkr"].to_numpy())
            log_diff = np.log(mismatched[diff_col].to_numpy())
            slope, intercept = np.polyfit(log_volume, log_diff, 1)
            predicted = slope * log_volume + intercept
            ss_res = float(np.sum((log_diff - predicted) ** 2))
            ss_tot = float(np.sum((log_diff - log_diff.mean()) ** 2))
            field_stats["loglog_slope"] = round(float(slope), 4)
            field_stats["loglog_r_squared"] = round(1.0 - ss_res / ss_tot, 4) if ss_tot > 0 else None
        else:
            field_stats["loglog_slope"] = None
            field_stats["loglog_r_squared"] = None
        field_stats["loglog_n"] = int(len(mismatched))

        stats[field] = field_stats

    per_bar = df[["segment", "dst_regime", "volume_ibkr", "volume_massive", "high_match", "low_match", "abs_diff_high", "abs_diff_low"]].reset_index()
    per_bar = per_bar.rename(columns={"index": "timestamp"})
    return per_bar, stats


def _safe_median(values: pd.Series) -> float | None:
    if len(values) == 0:
        return None
    return float(values.median())


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, dict, pd.DataFrame, dict]:
    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r} in the configured date range.")

    ibkr_df = _provider_frame(raw, "ibkr")
    massive_df = _provider_frame(raw, "massive")

    ibkr_df["segment"] = _segment_for_index(ibkr_df.index)
    ibkr_df["dst_regime"] = _dst_regime_for_index(ibkr_df.index)

    summary_rows = []
    joined_lag0 = None
    for lag in config.ALIGNMENT_LAGS_MINUTES:
        massive_shifted = massive_df.copy()
        massive_shifted.index = massive_shifted.index - pd.Timedelta(minutes=lag)
        massive_shifted = massive_shifted.rename(columns={"high": "high_massive", "low": "low_massive", "volume": "volume_massive"})

        joined = ibkr_df.rename(columns={"high": "high_ibkr", "low": "low_ibkr", "volume": "volume_ibkr"}).join(massive_shifted, how="inner")
        joined["high_match"] = joined["high_ibkr"] == joined["high_massive"]
        joined["low_match"] = joined["low_ibkr"] == joined["low_massive"]
        joined["both_match"] = joined["high_match"] & joined["low_match"]

        if lag == 0:
            joined_lag0 = joined.copy()

        grouped = joined.groupby(["segment", "dst_regime"])
        counts = grouped.size().reset_index(name="n_pairs")
        high_rate = grouped["high_match"].mean().reset_index(name="high_match_rate")
        low_rate = grouped["low_match"].mean().reset_index(name="low_match_rate")
        both_rate = grouped["both_match"].mean().reset_index(name="both_match_rate")

        merged = (
            counts.merge(high_rate, on=["segment", "dst_regime"]).merge(low_rate, on=["segment", "dst_regime"]).merge(both_rate, on=["segment", "dst_regime"])
        )
        merged["lag"] = lag
        merged["ticker"] = ticker
        summary_rows.append(merged)

    summary = pd.concat(summary_rows, ignore_index=True)
    summary["high_match_rate"] = (summary["high_match_rate"] * 100.0).round(3)
    summary["low_match_rate"] = (summary["low_match_rate"] * 100.0).round(3)
    summary["both_match_rate"] = (summary["both_match_rate"] * 100.0).round(3)
    summary["score"] = ((summary["high_match_rate"] + summary["low_match_rate"]) / 2.0).round(3)

    summary = summary[["ticker", "segment", "dst_regime", "lag", "n_pairs", "high_match_rate", "low_match_rate", "both_match_rate", "score"]]

    winners = {}
    for (segment, dst_regime), group in summary.groupby(["segment", "dst_regime"]):
        ranked = group.sort_values("score", ascending=False).reset_index(drop=True)
        winning_lag = int(ranked.loc[0, "lag"])
        winning_score = float(ranked.loc[0, "score"])
        runner_up_score = float(ranked.loc[1, "score"]) if len(ranked) > 1 else None
        margin = round(winning_score - runner_up_score, 3) if runner_up_score is not None else None
        clear_spike = bool(margin is not None and margin >= CLEAR_SPIKE_MARGIN_PCT)
        key = f"{segment}/{dst_regime}"
        winners[key] = {
            "winning_lag": winning_lag,
            "winning_score": winning_score,
            "runner_up_score": runner_up_score,
            "margin_pct": margin,
            "clear_spike": clear_spike,
            "n_pairs": int(ranked.loc[0, "n_pairs"]),
        }

    winning_lags = set()
    for key, result in winners.items():
        winning_lags.add(result["winning_lag"])

    verdict = {
        "winners": winners,
        "winning_lag_consistent": len(winning_lags) == 1,
        "consistent_winning_lag": next(iter(winning_lags)) if len(winning_lags) == 1 else None,
        "any_ambiguous": any(not r["clear_spike"] for r in winners.values()),
    }

    if joined_lag0 is None:
        raise RuntimeError("config.ALIGNMENT_LAGS_MINUTES must include 0 for the volume-correlation analysis.")
    volume_per_bar, volume_stats = _volume_correlation_analysis(joined_lag0)
    volume_per_bar["ticker"] = ticker

    return summary, verdict, volume_per_bar, volume_stats


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_summaries = []
    all_volume_per_bar = []
    ticker_verdicts = {}
    ticker_volume_stats = {}

    for ticker in config.TICKERS:
        summary, verdict, volume_per_bar, volume_stats = run_for_ticker(ticker)
        all_summaries.append(summary)
        all_volume_per_bar.append(volume_per_bar)
        ticker_verdicts[ticker] = verdict
        ticker_volume_stats[ticker] = volume_stats

        print(f"\n=== {ticker} ===")
        pivot = summary.pivot_table(index=["segment", "dst_regime"], columns="lag", values="score")
        print("Score (avg of high/low match rate, pct) by (segment, dst_regime) x lag:")
        print(pivot.to_string())
        print()
        for key, result in verdict["winners"].items():
            spike_label = "clear spike" if result["clear_spike"] else "AMBIGUOUS -- no clear spike"
            print(
                f"  {key}: winning lag={result['winning_lag']:+d}, score={result['winning_score']}%, "
                f"margin over runner-up={result['margin_pct']} pts ({spike_label}), n={result['n_pairs']}"
            )
        if verdict["winning_lag_consistent"]:
            lag = verdict["consistent_winning_lag"]
            if lag == 0:
                print("\nVerdict: winning lag is consistently 0 across every segment/regime -- no correction needed.")
            else:
                print(f"\nVerdict: winning lag is consistently {lag:+d} across every segment/regime -- load.py needs a correction.")
                print("Every later experiment must rerun on the corrected join.")
        else:
            print("\nVerdict: winning lag DIFFERS across segment/regime -- deeper mismatch than a single fixed offset.")
            print("Escalate before proceeding to E2+.")
        if verdict["any_ambiguous"]:
            print("Warning: at least one (segment, dst_regime) group has no clear spike (margin below the report threshold).")
            print("Inspect before trusting its winning lag.")

        print("\nVolume/disagreement correlation at lag 0 (ad-hoc addendum, not in the original E1 spec):")
        for field, field_stats in ticker_volume_stats[ticker].items():
            print(f"  {field}: matched median ibkr volume={field_stats['matched_median_volume']}, mismatched median={field_stats['mismatched_median_volume']}")
            print(f"    RTH only: matched median={field_stats['rth_matched_median_volume']}, mismatched median={field_stats['rth_mismatched_median_volume']}")
            print(f"    log-log regression on mismatched bars (n={field_stats['loglog_n']}):")
            print(f"      slope={field_stats['loglog_slope']}, R^2={field_stats['loglog_r_squared']}")

    summary_frame = pd.concat(all_summaries, ignore_index=True)
    summary_path = RESULTS_DIR / "lag_match_rate.parquet"
    summary_frame.to_parquet(summary_path, index=False)

    volume_frame = pd.concat(all_volume_per_bar, ignore_index=True)
    volume_path = RESULTS_DIR / "volume_correlation.parquet"
    volume_frame.to_parquet(volume_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/alignment/match_rate.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "lags_minutes": config.ALIGNMENT_LAGS_MINUTES,
            "clear_spike_margin_pct": CLEAR_SPIKE_MARGIN_PCT,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(summary_path), str(volume_path)],
            "verdicts": ticker_verdicts,
            "volume_correlation": ticker_volume_stats,
        },
    )

    print(f"\nWrote {summary_path}")
    print(f"Wrote {volume_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
