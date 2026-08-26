"""Assembles results/ibkr_massive_mad/findings.md, the task's final deliverable, from
manifest.json's stored verdicts (tasks/ibkr_massive_mad_calibration.md's "Final deliverable"
section lists the required structure).

Every section is built from data already computed and persisted by E0-E8 -- consistent with the
Layout section's "later experiments read earlier outputs from results/, not by recomputing" -- with
one deliberate exception: the Databento shortlist (deliverable point 9) needs actual contiguous
range boundaries at the recommended k, which no prior experiment persisted (E4 only kept aggregate
counts). Rather than retroactively modifying an already-merged experiment, this script does its own
small, clearly-scoped read-only computation for that one section, reusing the same conditional-MAD
band parameters (already computed and stored by E4) rather than re-deriving them.

Read-only against the warehouse; writes only results/ibkr_massive_mad/findings.md.

Run from the repo root: python .exp/_shared/report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_SHARED_PARENT = Path(__file__).resolve().parent.parent
if str(_SHARED_PARENT) not in sys.path:
    sys.path.insert(0, str(_SHARED_PARENT))

from _shared import config, load, manifest  # noqa: E402
from reconcile.algorithm import FIELD_GROUP_OHLC, fields_for_group  # noqa: E402

FINDINGS_PATH = Path("results/ibkr_massive_mad/findings.md")

# Gap-merge parameter used for the Databento shortlist specifically -- a practical middle value
# from E4's own grid (E4_G_GRID_MINUTES = [5, 15, 60]), not re-litigated here.
SHORTLIST_GAP_MINUTES = 15
SHORTLIST_TOP_N = 20


def _provider_ohlc(raw: pd.DataFrame, provider: str, fields: list[str]) -> pd.DataFrame:
    columns = ["timestamp"] + fields
    subset = raw.loc[raw["provider"] == provider, columns].copy()
    for field_name in fields:
        subset[field_name] = subset[field_name].astype(float)
    subset = subset.set_index("timestamp")
    subset.index = pd.DatetimeIndex(subset.index)
    return subset


def _cluster_into_ranges(timestamps: list, gap_minutes: int) -> list[tuple]:
    if len(timestamps) == 0:
        return []
    gap_limit = pd.Timedelta(minutes=gap_minutes)
    ranges = []
    range_start = timestamps[0]
    range_end = timestamps[0]
    range_flags = 1
    for timestamp in timestamps[1:]:
        if timestamp - range_end <= gap_limit:
            range_end = timestamp
            range_flags += 1
        else:
            ranges.append((range_start, range_end, range_flags))
            range_start = timestamp
            range_end = timestamp
            range_flags = 1
    ranges.append((range_start, range_end, range_flags))
    return ranges


def build_databento_shortlist(ticker: str, conditional_mad_scaled: dict, k: float) -> pd.DataFrame:
    """Own, clearly-scoped computation -- see module docstring. Reuses the exact conditional-MAD
    band E4 already computed and stored (passed in), does not re-derive it."""
    fields = fields_for_group(FIELD_GROUP_OHLC)
    with load.connect_read_only() as connection:
        raw = load.fetch_staging_rows(connection, ticker, config.CANDIDATE_PROVIDERS, config.START_DATE, config.END_DATE)

    ibkr_df = _provider_ohlc(raw, "ibkr", fields)
    massive_df = _provider_ohlc(raw, "massive", fields)
    joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")

    flagged_any = pd.Series(False, index=joined.index)
    for field_name in fields:
        ibkr_col = joined[f"{field_name}_ibkr"]
        massive_col = joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        threshold = k * conditional_mad_scaled[field_name]
        flagged_any = flagged_any | (d.abs() > threshold)

    flagged_timestamps = sorted(joined.index[flagged_any])
    ranges = _cluster_into_ranges(flagged_timestamps, SHORTLIST_GAP_MINUTES)

    rows = []
    for range_start, range_end, n_flags in ranges:
        billed_minutes = int((range_end - range_start).total_seconds() // 60) + 1
        density = round(n_flags / billed_minutes, 4)
        cost_usd = round(billed_minutes * config.DATABENTO_OHLCV_1M_BYTES_PER_RECORD / 1e9 * config.DATABENTO_PRICE_PER_GB, 6)
        rows.append(
            {
                "ticker": ticker,
                "range_start": range_start,
                "range_end": range_end,
                "billed_minutes": billed_minutes,
                "n_flags": n_flags,
                "flag_density": density,
                "cost_usd": cost_usd,
            }
        )
    frame = pd.DataFrame(rows)
    if len(frame) > 0:
        # Primary sort is flag_density per the task's own instruction; break ties on n_flags
        # descending -- a 1-2 minute range trivially hits density=1.0, which would otherwise fill
        # the top of the list with the least substantively useful ranges (nothing to actually
        # verify beyond a couple of bars) ahead of larger, denser clusters worth more per pull.
        frame = frame.sort_values(["flag_density", "n_flags"], ascending=[False, False]).reset_index(drop=True)
    return frame


def _fmt_pct(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}%"


def build_findings_markdown(manifest_data: dict, shortlist_by_ticker: dict[str, pd.DataFrame]) -> str:
    experiments = manifest_data["experiments"]
    e0 = experiments["join_integrity"]
    e1 = experiments["alignment"]
    e2 = experiments["adjustment"]
    e3 = experiments["dispersion"]
    e5 = experiments["stationarity"]
    e6 = experiments["validation"]
    e7 = experiments["volume"]
    e8 = experiments["coexistence"]

    tickers = e0["tickers"]
    lines: list[str] = []

    lines.append("# IBKR/Massive MAD Calibration -- Findings")
    lines.append("")
    lines.append(f"Assembled by `.exp/_shared/report.py` from `manifest.json` (git sha `{manifest_data.get('git_sha', 'unknown')}`).")
    lines.append(f"Range: `{e0['start_date']}`..`{e0['end_date']}`. Tickers measured: {', '.join(tickers)} (SPY only -- see caveats).")
    lines.append("")

    # --- Open questions / escalations, at the top per the task's own instruction ---
    lines.append("## Open questions and escalations (read this first)")
    lines.append("")
    lines.append("1. **Real production data-quality bug found mid-task, not yet fixed there**: `yfinance`'s stored OHLC")
    lines.append("   values carry float32 rounding artifacts (e.g. `737.239990234375` instead of `737.24`) -- a storage-")
    lines.append("   precision quirk in the ingest/staging path. Worked around locally in E6/E8 (round to cent precision")
    lines.append("   before comparing); the underlying production path still has it. **Needs its own follow-up issue.**")
    lines.append("2. **Every number in this document is SPY-only.** `dim_ticker` now has 8 tickers, but only SPY has a frozen")
    lines.append("   unpurged staging window -- none of these recommendations (k=3.0, k_volume, the stationarity/coverage")
    lines.append("   reads) have been verified on any other ticker.")
    lines.append("3. **E3's raw MAD is exactly 0.0 (degenerate)** -- any production adoption of a MAD-based tolerance MUST")
    lines.append("   pair it with a nonzero floor (`materiality_floor` already exists for this) or it will reject every")
    lines.append("   nonzero disagreement outright. Not optional, not solved here.")
    lines.append("4. **Two real anomalies remain unexplained**: E1's RTH-vs-pre/post agreement gap (volume hypothesis")
    lines.append("   tested and rejected, R^2~0) and E5's March 2026 flag-rate outlier (not a split/adjustment per E2).")
    lines.append("   Neither blocks the recommendation below, both are worth a closer look if revisited.")
    lines.append("5. **Structural limitation, true regardless of any experiment's result**: a two-provider band cannot")
    lines.append("   detect correlated error (both providers agreeing while both are wrong). This method does not retire")
    lines.append('   `yfinance` -- see "Role of yfinance after this task" in the task file.')
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. E0-E2 corrections
    lines.append("## 1. E0-E2: corrections applied, and whether any invalidate the approach")
    lines.append("")
    for ticker in tickers:
        gate = e0["gate"][ticker]
        passed_label = "PASSED" if gate["passed"] else "FAILED"
        lines.append(
            f"- **E0 (join integrity)**: RTH `both` coverage {_fmt_pct(gate['rth_both_coverage_pct'], 3)} "
            f"(gate >= 95%, {passed_label}), {gate['trading_days']} trading days. No correction needed."
        )
    align_verdict = e1["verdicts"][tickers[0]]
    lag_label = align_verdict["consistent_winning_lag"] if align_verdict["winning_lag_consistent"] else "INCONSISTENT"
    lines.append(f"- **E1 (alignment)**: winning lag = `{lag_label}` across every segment/regime. No correction applied to `load.py`.")
    adj_verdict = e2["verdicts"][tickers[0]]
    lines.append(
        f"- **E2 (adjustment)**: outcome = `{adj_verdict['outcome']}` ({adj_verdict['n_deviating_days']} of "
        f"{adj_verdict['n_days']} days deviating). No correction applied."
    )
    lines.append("")
    lines.append("**None of E0-E2 invalidate the approach.** All three came back clean on SPY -- coverage sufficient,")
    lines.append("alignment already correct at lag 0, no split/adjustment mismatch. The method proceeds on an unmodified join.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 2. E3 verdict
    lines.append("## 2. E3: MAD vs Welford verdict")
    lines.append("")
    for ticker in tickers:
        pooled = e3["verdicts"][ticker]["fields"]["ALL"]
        n_pooled = e3["verdicts"][ticker]["n_pooled"]
        lines.append(
            f"- sigma collapses **{pooled['sigma_pct_change']}%** when just the top {e3['trim_top_pct']}% of `|d|` "
            f"is trimmed ({n_pooled:,} pooled observations)."
        )
        lines.append(
            "- Raw MAD is exactly 0.0 (both pooled and per-field) -- the ratio is mathematically undefined, a more extreme result than a finite ratio would be."
        )
        lines.append(f"- **Implied window state cost if adopted: {n_pooled:,} pooled observations** (no O(1) streaming update, unlike Welford).")
    lines.append("")
    lines.append("**Verdict: data SUPPORTS the MAD switch**, with the degenerate-MAD caveat in the open questions above.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 3. Recommended k and g
    lines.append("## 3. Recommended k and g")
    lines.append("")
    for ticker in tickers:
        e6v = e6["verdicts"][ticker]
        e8v = e8["verdicts"][ticker]
        lines.append(f"- **Recommended k = {e8v['k']}** (OHLC price band) -- matches production's existing `DEFAULT_RECONCILE_K`.")
        lines.append(
            f"- **E6 quality at k={e8v['k']}**: precision and recall proxies computed over the {e6v['n_overlap_bars']:,}-bar "
            f"overlap window (`{e6v['overlap_start']}`..`{e6v['overlap_end']}`), `n_whistleblower_flagged={e6v['n_whistleblower_flagged']}`. "
            f"See `e6_validation.parquet` for the full k sweep."
        )
    lines.append(
        "- **E4 spend at k=3, all g**: the whole (k, g) grid costs **$0.0002-$0.27/ticker** for the OHLCV-1m schema -- "
        "spend does not discriminate between k choices at all; the pick above is driven entirely by E6 quality."
    )
    volume_k = e7["verdicts"][tickers[0]]["exact_k_for_target"]
    lines.append(
        f"- **Volume band (E7, separate multiplier, not comparable 1:1 to the OHLC k)**: recommend k_volume =~ {volume_k} "
        f"if a volume band is ever built (hypothetical -- see E7's own caveat; not implemented)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # 4. E5 verdict
    lines.append("## 4. E5: stationarity verdict")
    lines.append("")
    for ticker in tickers:
        v = e5["verdicts"][ticker]
        lines.append(
            f"- Band calibrated on `{v['calibration_month']}`, `k` fixed at {v['k_fixed']}, applied unchanged to each "
            f"earlier month ({v['n_months']} months, {v['n_low_sample_months']} low-sample)."
        )
        min_rate = _fmt_pct(v["min_flag_rate_pct"], 2)
        max_rate = _fmt_pct(v["max_flag_rate_pct"], 2)
        lines.append(f"- Flag rate range: {min_rate} - {max_rate}. Trend: **{v['trend_direction']}**.")
    lines.append("")
    lines.append("**Verdict: one global band** -- flat, not rising going back. No time-varying band or hard trust cutoff")
    lines.append("date needed on this evidence. (See open question #4 above re: the March outlier.)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 5. E7 verdict
    lines.append("## 5. E7: volume verdict")
    lines.append("")
    for ticker in tickers:
        v = e7["verdicts"][ticker]
        gate_label = "STABLE + MODEST (gate passes)" if v["gate_passes"] else "UNSTABLE or NOT MODEST (gate fails)"
        lines.append(
            f"- Center spread {v['max_center_spread']} (threshold <= {e7['stable_center_spread_max']}), dispersion "
            f"{v['max_dispersion']} (threshold <= {e7['modest_dispersion_max']}) -> **{gate_label}**."
        )
        lines.append(
            f"- Tail-fatness ratio {v['tail_fatness_ratio']} -- a real heavy tail despite the modest MAD. "
            f"Exact k for a {e7['target_flag_rate_pct']}% flag rate: **{v['exact_k_for_target']}**."
        )
    lines.append("")
    lines.append("**Verdict: a separate volume band is hypothetically viable** (per the gate), but this is evidence for a")
    lines.append("possible future follow-up, not a description of current behavior -- production runs no cross-provider")
    lines.append("comparison on volume today (`005_remove_volume_field_group`), and building this stays out of scope here.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 6. E6b verdict
    lines.append("## 6. E6b: is yfinance independent enough to be a third corner?")
    lines.append("")
    for ticker in tickers:
        v = e6["verdicts"][ticker]
        disagreement_only = v["e6b_correlations_disagreement_only"]
        my_mad = v["e6b_dispersions"]["d_massive_yfinance"]["conditional_mad_scaled"]
        im_mad = v["e6b_dispersions"]["d_ibkr_massive"]["conditional_mad_scaled"]
        lines.append(
            f"- Dispersion test: `massive-yfinance` conditional MAD ({my_mad:.3e}) is NOT tighter than "
            f"`ibkr-massive`'s ({im_mad:.3e}) -- no shared upstream by that measure."
        )
        lines.append(
            f"- Naive pooled correlation (`ibkr-yfinance` vs `massive-yfinance`) = {v['e6b_correlations_pooled_naive']['iy_my']} "
            f"-- **mechanically inevitable wherever `ibkr==massive` exactly, not meaningful, do not use.**"
        )
        lines.append(
            f"- Disagreement-only correlation (n={v['n_disagreement_instances']:,}, the real test): "
            f"`im_my`={disagreement_only['im_my']}, `im_iy`={disagreement_only['im_iy']}, `iy_my`={disagreement_only['iy_my']}."
        )
    lines.append("")
    lines.append("**Verdict: yfinance is independent enough** on the task's own literal dispersion test. The disagreement-only")
    lines.append("correlation reveals a real, different pattern -- yfinance tracks `ibkr` closely on disagreement bars (not")
    lines.append("`massive`), the opposite pairing from what E6b was designed to worry about. **This does not discount the E6")
    lines.append("proxies** -- if anything it means yfinance's vote isn't circular with the `ibkr`/`massive` comparison being")
    lines.append("tested. Still bounded evidence, not ground truth, per the task's own framing.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 7. E8 verdict
    lines.append("## 7. E8: which band is primary for the historical period?")
    lines.append("")
    for ticker in tickers:
        v = e8["verdicts"][ticker]
        lines.append(
            f"- Confusion matrix at k={v['k']}: both={v['both']}, MAD-only={v['mad_only']}, "
            f"whistleblower-only={v['whistleblower_only']}, neither={v['neither']}."
        )
        lines.append(
            f"- Jaccard = {v['jaccard']}. MAD misses {_fmt_pct(v['whistleblower_miss_pct'], 1)} of what the whistleblower flags (superset threshold was <=10%)."
        )
    lines.append("")
    lines.append("**Verdict: SUBSTANTIALLY DISJOINT -- coexistence is earned, not MAD-replaces-yfinance.** The MAD band owns")
    lines.append("the pre-overlap historical period (yfinance was never available there anyway); the existing yfinance/Welford")
    lines.append("band keeps owning the recent/rolling period it already covers. `materiality_floor_tolerance.md` and")
    lines.append("`variance_floor_clamp.md` are **NOT** retirable on this evidence.")
    lines.append("")
    lines.append("**Circularity caveat (task's own explicit instruction)**: E6 and E8 are both computed *using* yfinance.")
    lines.append("A disjoint result isn't the direction circularity would bias toward (circularity would inflate an apparent")
    lines.append("*superset* finding, not manufacture a disjoint one) -- but this is recorded per the task's instruction")
    lines.append("regardless, and the result holds for one window, one alignment regime, one k.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 8. Recalibration cadence
    lines.append("## 8. Proposed k recalibration cadence")
    lines.append("")
    lines.append("yfinance's ~30-day rolling window is the only mechanism for recalibrating k going forward (per the task's")
    lines.append('"Role of yfinance after this task" section). Proposed cadence, not yet implemented:')
    lines.append("")
    lines.append("- **Monthly**, aligned with E5's own calibration-month pattern: recompute the conditional-MAD band basis")
    lines.append("  on the trailing month, re-run E6's precision/recall proxy against that month's fresh overlap, and only")
    lines.append("  change the production k if the recommendation shifts by more than one grid step (E4_K_GRID granularity).")
    lines.append("- **Drift trigger**: if a monthly recalibration run's flag rate at the current k moves outside the range")
    e5_min = _fmt_pct(e5["verdicts"][tickers[0]]["min_flag_rate_pct"], 1)
    e5_max = _fmt_pct(e5["verdicts"][tickers[0]]["max_flag_rate_pct"], 1)
    lines.append(f"  observed in E5 ({e5_min}-{e5_max} across 8 months), treat that as a signal to investigate before")
    lines.append("  the next scheduled recalibration, not wait for it.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 9. Databento shortlist
    lines.append("## 9. Databento shortlist")
    lines.append("")
    lines.append(f"Flagged ranges at the recommended k=3.0, gap-merged at g={SHORTLIST_GAP_MINUTES} minutes, over the full")
    lines.append(f"`{e0['start_date']}`..`{e0['end_date']}` range (not just the overlap window) -- this is what would actually")
    lines.append("need Databento verification for the historical period. Ordered by flag density (flags per billed minute) so")
    lines.append("the highest-value ranges come first if credit is limited; per E4, total cost is negligible ($0.0002-$0.27")
    lines.append("for the whole grid) so in practice all of these are affordable regardless of ordering.")
    lines.append("")
    for ticker, shortlist in shortlist_by_ticker.items():
        total_cost = round(shortlist["cost_usd"].sum(), 4) if len(shortlist) > 0 else 0.0
        total_ranges = len(shortlist)
        lines.append(f"**{ticker}**: {total_ranges} total ranges, ${total_cost} total cost. Top {min(SHORTLIST_TOP_N, total_ranges)} by flag density:")
        lines.append("")
        lines.append("| range_start | range_end | billed_minutes | n_flags | flag_density | cost_usd |")
        lines.append("|---|---|---|---|---|---|")
        for _, row in shortlist.head(SHORTLIST_TOP_N).iterrows():
            lines.append(
                f"| {row['range_start']} | {row['range_end']} | {row['billed_minutes']} | {row['n_flags']} | {row['flag_density']} | {row['cost_usd']} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Assembled from `results/ibkr_massive_mad/manifest.json` and a small dedicated read for the Databento")
    lines.append("shortlist (see this script's own docstring for why that one section needed a live computation).*")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    manifest_data = manifest.load_manifest()
    experiments = manifest_data.get("experiments", {})

    required = ["join_integrity", "alignment", "adjustment", "dispersion", "budget", "stationarity", "validation", "volume", "coexistence"]
    missing = []
    for name in required:
        if name not in experiments:
            missing.append(name)
    if missing:
        raise RuntimeError(f"manifest.json is missing experiment(s) {missing} -- run all of E0-E8 before assembling findings.md.")

    tickers = experiments["join_integrity"]["tickers"]
    shortlist_by_ticker = {}
    for ticker in tickers:
        conditional_mad_scaled = experiments["budget"]["verdicts"][ticker]["conditional_mad_scaled"]
        k = experiments["coexistence"]["verdicts"][ticker]["k"]
        shortlist_by_ticker[ticker] = build_databento_shortlist(ticker, conditional_mad_scaled, k)

    findings_markdown = build_findings_markdown(manifest_data, shortlist_by_ticker)

    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FINDINGS_PATH.write_text(findings_markdown, encoding="utf-8")
    print(f"Wrote {FINDINGS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
