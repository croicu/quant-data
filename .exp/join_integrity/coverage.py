"""E0 -- join integrity (tasks/ibkr_massive_mad_calibration.md).

Per (ticker, minute), classifies staging_market_data_1min coverage into both / ibkr_only /
massive_only / neither, aggregated by session segment (pre/RTH/post) and by ET calendar month.
Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/join_integrity/coverage.py
"""

from __future__ import annotations

import sys
from datetime import time as time_type
from pathlib import Path

import pandas as pd

_SHARED_PARENT = Path(__file__).resolve().parent.parent
if str(_SHARED_PARENT) not in sys.path:
    sys.path.insert(0, str(_SHARED_PARENT))

from _shared import config, load, manifest  # noqa: E402

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md). Underscore-private
# in reconcile/cli.py; imported anyway per the task's explicit instruction.
from reconcile.cli import _EASTERN, _MARKET_CLOSE, _MARKET_OPEN, _session_segment  # noqa: E402

EXPERIMENT_NAME = "join_integrity"
RESULTS_DIR = Path("results/ibkr_massive_mad") / EXPERIMENT_NAME

RTH_COVERAGE_GATE_PCT = 95.0

_SEGMENT_LABELS = {0: "pre", 1: "rth", 2: "post"}


def _classify_row(has_ibkr: bool, has_massive: bool) -> str:
    if has_ibkr and has_massive:
        return "both"
    if has_ibkr:
        return "ibkr_only"
    if has_massive:
        return "massive_only"
    return "neither"


def _build_expected_grid(trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    """Every (day, minute) pair in the configured 4:00-20:00 ET extended-hours window, for each
    trading day. This is the experiment's own assumption about the expected minute universe (see
    config.py's PRE_POST_GRID_START/END_HOUR_ET) -- not a claim that every such minute must
    genuinely have a trade."""
    start_minute = config.PRE_POST_GRID_START_HOUR_ET * 60
    end_minute = config.PRE_POST_GRID_END_HOUR_ET * 60
    minutes_of_day = pd.RangeIndex(start_minute, end_minute + 1)

    grid_index = pd.MultiIndex.from_product([trading_days, minutes_of_day], names=["et_date", "et_minute_of_day"])
    grid = grid_index.to_frame(index=False)
    return grid


def _segment_for_et_minute(et_minute_of_day: int) -> str:
    """Same comparison _session_segment makes, applied directly in ET rather than round-tripping
    through UTC -- the expected grid is ET-native by construction (config.py), so there is no UTC
    timestamp to convert. Boundary values (_MARKET_OPEN, _MARKET_CLOSE) are the same imported
    constants _session_segment itself uses, not redefined here."""
    et_time = time_type(et_minute_of_day // 60, et_minute_of_day % 60)
    if et_time < _MARKET_OPEN:
        return "pre"
    if et_time <= _MARKET_CLOSE:
        return "rth"
    return "post"


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    row_counts = {}
    for provider_name in config.CANDIDATE_PROVIDERS:
        row_counts[provider_name] = int((raw["provider"] == provider_name).sum())

    if raw.empty:
        raise RuntimeError(f"No staging rows found for ticker={ticker!r}, providers={config.CANDIDATE_PROVIDERS!r} in the configured date range.")

    raw["timestamp_utc"] = pd.to_datetime(raw["timestamp"]).dt.tz_localize("UTC")
    raw["timestamp_et"] = raw["timestamp_utc"].dt.tz_convert(_EASTERN)
    raw["et_date"] = raw["timestamp_et"].dt.normalize().dt.tz_localize(None)
    raw["et_minute_of_day"] = raw["timestamp_et"].dt.hour * 60 + raw["timestamp_et"].dt.minute
    raw["segment_code"] = raw["timestamp"].apply(_session_segment)
    raw["segment"] = raw["segment_code"].map(_SEGMENT_LABELS)

    # Trading days = ET dates where either provider reported at least one RTH bar. Determined
    # from the data itself, not an external holiday calendar -- deliberately avoids a market
    # holiday masquerading as a "neither" gap under a naive weekday assumption.
    rth_rows = raw[raw["segment"] == "rth"]
    trading_days = pd.DatetimeIndex(sorted(rth_rows["et_date"].unique()))
    if len(trading_days) == 0:
        raise RuntimeError(f"No RTH staging rows found for ticker={ticker!r} -- cannot determine trading days.")

    grid = _build_expected_grid(trading_days)
    grid["segment"] = grid["et_minute_of_day"].apply(_segment_for_et_minute)

    ibkr_present = raw.loc[raw["provider"] == "ibkr", ["et_date", "et_minute_of_day"]].drop_duplicates()
    massive_present = raw.loc[raw["provider"] == "massive", ["et_date", "et_minute_of_day"]].drop_duplicates()
    ibkr_present["has_ibkr"] = True
    massive_present["has_massive"] = True

    grid = grid.merge(ibkr_present, on=["et_date", "et_minute_of_day"], how="left")
    grid = grid.merge(massive_present, on=["et_date", "et_minute_of_day"], how="left")
    grid["has_ibkr"] = grid["has_ibkr"].fillna(False)
    grid["has_massive"] = grid["has_massive"].fillna(False)

    classifications = []
    for has_ibkr_value, has_massive_value in zip(grid["has_ibkr"], grid["has_massive"]):
        classifications.append(_classify_row(has_ibkr_value, has_massive_value))
    grid["classification"] = classifications

    grid["ticker"] = ticker
    grid["month"] = grid["et_date"].dt.to_period("M").astype(str)

    by_minute = grid[["ticker", "et_date", "et_minute_of_day", "segment", "month", "classification"]].copy()

    summary = grid.groupby(["ticker", "segment", "month", "classification"]).size().reset_index(name="minute_count")
    totals = summary.groupby(["ticker", "segment", "month"])["minute_count"].transform("sum")
    summary["pct"] = (summary["minute_count"] / totals * 100.0).round(3)

    rth_summary = summary[summary["segment"] == "rth"]
    rth_both = rth_summary[rth_summary["classification"] == "both"]["minute_count"].sum()
    rth_total = rth_summary["minute_count"].sum()
    rth_both_pct = float(round((rth_both / rth_total * 100.0), 3)) if rth_total > 0 else 0.0
    gate_passed = bool(rth_both_pct >= RTH_COVERAGE_GATE_PCT)

    gate_result = {
        "rth_both_coverage_pct": rth_both_pct,
        "gate_threshold_pct": RTH_COVERAGE_GATE_PCT,
        "passed": gate_passed,
        "trading_days": int(len(trading_days)),
        "row_counts": row_counts,
    }
    return by_minute, summary, gate_result


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_by_minute = []
    all_summary = []
    gate_results = {}
    overall_pass = True

    for ticker in config.TICKERS:
        by_minute, summary, gate_result = run_for_ticker(ticker)
        all_by_minute.append(by_minute)
        all_summary.append(summary)
        gate_results[ticker] = gate_result
        if not gate_result["passed"]:
            overall_pass = False

        print(f"\n=== {ticker} ===")
        print(f"Trading days: {gate_result['trading_days']}")
        print(f"Row counts: {gate_result['row_counts']}")
        print(
            f"RTH both-coverage: {gate_result['rth_both_coverage_pct']}% (gate: >= {RTH_COVERAGE_GATE_PCT}%) -- {'PASS' if gate_result['passed'] else 'FAIL'}"
        )
        print("\nCoverage matrix (segment x classification, pct of expected minutes, whole date range):")
        # Deliberately re-aggregated across the whole range here rather than pivoting the
        # per-month `summary` rows directly -- pivoting `pct` (already a within-month percentage)
        # would average across months of very different sizes and misrepresent the true overall
        # split. `summary_frame`'s own per-month rows (written to parquet below) stay the
        # fine-grained source of truth for later experiments.
        ticker_by_minute = by_minute[by_minute["ticker"] == ticker]
        overall_counts = ticker_by_minute.groupby(["segment", "classification"]).size().reset_index(name="minute_count")
        overall_totals = overall_counts.groupby("segment")["minute_count"].transform("sum")
        overall_counts["pct"] = (overall_counts["minute_count"] / overall_totals * 100.0).round(3)
        pivot = overall_counts.pivot_table(index="segment", columns="classification", values="pct", fill_value=0.0)
        print(pivot.to_string())

    by_minute_frame = pd.concat(all_by_minute, ignore_index=True)
    summary_frame = pd.concat(all_summary, ignore_index=True)

    by_minute_path = RESULTS_DIR / "coverage_by_minute.parquet"
    summary_path = RESULTS_DIR / "coverage_by_segment_month.parquet"
    by_minute_frame.to_parquet(by_minute_path, index=False)
    summary_frame.to_parquet(summary_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/join_integrity/coverage.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "ibkr_method_pin": config.IBKR_METHOD,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "pre_post_grid_hours_et": [config.PRE_POST_GRID_START_HOUR_ET, config.PRE_POST_GRID_END_HOUR_ET],
            "outputs": [str(by_minute_path), str(summary_path)],
            "gate": gate_results,
            "gate_passed_all_tickers": overall_pass,
        },
    )

    print(f"\nWrote {by_minute_path}")
    print(f"Wrote {summary_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")

    if not overall_pass:
        print(f"\nGATE FAILED for at least one ticker (RTH both-coverage below {RTH_COVERAGE_GATE_PCT}%) -- stop and investigate before proceeding to E1.")
        return 1
    print("\nGate passed for all tickers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
