"""E6 (+E6b) -- semi-labeled validation on the overlap month (tasks/ibkr_massive_mad_calibration.md).

The overlap window is where ibkr, massive, AND yfinance (ACCEPTED rows only) all have data at
lag 0 -- in this warehouse that's only ~5 real days
(config.START_DATE..config.END_DATE intersected with yfinance's own trailing-30-day availability),
not a full month. Reported honestly, not treated as resolved -- see config.py's WHISTLEBLOWER_
PROVIDER comment.

E6 precision proxy: of bars the ibkr/massive MAD band (same conditional-MAD basis as E4, swept
over the same k grid) flags, what fraction does yfinance decisively side with one candidate on
(vs sit as noise between the two)? "Decisive" is defined in config.E6_DECISIVE_MULTIPLE's comment
and printed below -- not assumed obvious.

E6 recall proxy: of bars the *existing* yfinance whistleblower flags against ibkr -- reusing
reconcile.algorithm's actual `_agrees_within_tolerance`/`_tolerance` with the real, currently
calibrated stddev from provider_pair_disagreement (not a redefinition) -- what fraction does the
MAD band also catch, per k?

E6b: computes all three pairwise difference series (ibkr-massive, ibkr-yfinance,
massive-yfinance) over the same overlap window and compares their dispersion side by side, plus
pairwise correlations, to test whether yfinance is genuinely independent of massive or whether
they likely share an upstream feed (which would weaken both proxies above).

Framing (task's own words): these are BOUNDED PROXIES, not ground truth. yfinance remains a
whistleblower whose disagreement is evidence, not verdict.

Read-only against the warehouse; writes only under results/ibkr_massive_mad/.

Run from the repo root: python .exp/validation/overlap_validation.py
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

# Reused, not redefined -- invariant 3 (tasks/ibkr_massive_mad_calibration.md). _agrees_within_
# tolerance/_tolerance are the actual production Tier 2/3 mechanism -- reused so the recall
# proxy's "existing yfinance whistleblower" flag decision is the real one, not a reimplementation.
from reconcile.algorithm import (  # noqa: E402
    DATA_QUALITY_ACCEPTED,
    FIELD_GROUP_OHLC,
    ROLE_CANDIDATE,
    ROLE_WHISTLEBLOWER,
    FieldTolerance,
    ProviderBar,
    _agrees_within_tolerance,
    batch_stats,
    fields_for_group,
    stddev_from_stats,
)

EXPERIMENT_NAME = "validation"
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
            # yfinance's stored OHLC values carry float32 rounding artifacts (confirmed live,
            # e.g. 737.239990234375 instead of 737.24) -- not real price noise, a storage-
            # precision quirk somewhere in the ingest/staging path. Left uncorrected this
            # contaminates every yfinance-involving comparison below (both the precision proxy's
            # "typical deviation" baseline and E6b's independence test) with sub-cent noise that
            # swamps genuine signal on the majority of bars where ibkr/massive already agree
            # exactly. Rounding to cent precision (matching ibkr/massive's own observed
            # precision) removes the artifact without touching genuine disagreement, which is
            # always far larger than a fraction of a cent. Worth flagging as a real, previously
            # unknown data-quality finding in the production yfinance path -- not fixed there,
            # out of scope per this task's Non-goals, but noted for a follow-up.
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


def _conditional_median(values: pd.Series) -> float:
    """Median restricted to nonzero observations -- same "conditional" convention as
    `_conditional_mad_scaled` above, for the same reason: 82-89% of pooled yfinance-vs-candidate
    deviations are exact ties (yfinance matches one candidate to the cent), so a plain median over
    all instances collapses to exactly 0.0 for every field (confirmed live, 2026-08-26 pre-report
    review). That degenerate 0.0 silently changed what "decisive" meant -- with typical=0, decisive
    reduced to "closer deviation is an exact tie, farther deviation is any nonzero value" rather
    than a real noise-scaled threshold, which is what produced E6's originally-reported
    non-monotonic precision curve (50.4% -> 38.2% -> 82.7% at k=1/2/3)."""
    nonzero = values[values != 0]
    if len(nonzero) == 0:
        return 0.0
    return float(nonzero.median())


def _pairwise_d(a: pd.Series, b: pd.Series) -> pd.Series:
    reference = (a + b) / 2.0
    return (a - b) / reference


def run_for_ticker(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
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

    # Full-range conditional MAD basis -- same computation, same data range as E4, so the band
    # being quality-scored here is the identical band E4 characterized for spend.
    im_joined = ibkr_df.join(massive_df, how="inner", lsuffix="_ibkr", rsuffix="_massive")
    conditional_mad_scaled = {}
    for field_name in fields:
        ibkr_col = im_joined[f"{field_name}_ibkr"]
        massive_col = im_joined[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        conditional_mad_scaled[field_name] = _conditional_mad_scaled(d.to_numpy())

    # Triple overlap: only where ibkr, massive, AND an ACCEPTED yfinance row all exist at lag 0.
    yf_rename_map = {}
    for field_name in fields:
        yf_rename_map[field_name] = f"{field_name}_yf"
    yfinance_renamed = yfinance_accepted[fields].rename(columns=yf_rename_map)
    triple = im_joined.join(yfinance_renamed, how="inner")

    if len(triple) == 0:
        raise RuntimeError(f"No triple-overlap bars (ibkr + massive + ACCEPTED yfinance) found for ticker={ticker!r}.")

    overlap_start = triple.index.min()
    overlap_end = triple.index.max()

    abs_d_im = {}
    for field_name in fields:
        ibkr_col = triple[f"{field_name}_ibkr"]
        massive_col = triple[f"{field_name}_massive"]
        reference = (ibkr_col + massive_col) / 2.0
        d = (ibkr_col - massive_col) / reference
        abs_d_im[field_name] = d.abs()
    abs_d_im_frame = pd.DataFrame(abs_d_im, index=triple.index)

    # --- Precision proxy: "decisive" per (bar, field) ---
    typical_dev = {}
    decisive_by_field = {}
    for field_name in fields:
        ibkr_col = triple[f"{field_name}_ibkr"]
        massive_col = triple[f"{field_name}_massive"]
        yf_col = triple[f"{field_name}_yf"]
        reference = (ibkr_col + massive_col) / 2.0
        dev_ibkr = (yf_col - ibkr_col).abs() / reference
        dev_massive = (yf_col - massive_col).abs() / reference
        typical = _conditional_median(pd.concat([dev_ibkr, dev_massive]))
        typical_dev[field_name] = typical
        closer = pd.concat([dev_ibkr, dev_massive], axis=1).min(axis=1)
        farther = pd.concat([dev_ibkr, dev_massive], axis=1).max(axis=1)
        decisive_by_field[field_name] = (farther > config.E6_DECISIVE_MULTIPLE * typical) & (closer <= typical)
    decisive_frame = pd.DataFrame(decisive_by_field, index=triple.index)

    # --- Recall proxy: reproduce the real production whistleblower flag decision ---
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
    triple = triple.copy()
    triple["whistleblower_flagged"] = whistleblower_flags
    n_whistleblower_flagged = int(triple["whistleblower_flagged"].sum())

    # --- Sweep k: precision + recall per k ---
    validation_rows = []
    for k in config.E4_K_GRID:
        total_flagged = 0
        total_decisive = 0
        mad_flagged_any = pd.Series(False, index=triple.index)
        for field_name in fields:
            field_flagged = abs_d_im_frame[field_name] > k * conditional_mad_scaled[field_name]
            total_flagged += int(field_flagged.sum())
            total_decisive += int((field_flagged & decisive_frame[field_name]).sum())
            mad_flagged_any = mad_flagged_any | field_flagged

        precision_pct = round(total_decisive / total_flagged * 100.0, 3) if total_flagged > 0 else None
        n_caught = int((triple["whistleblower_flagged"] & mad_flagged_any).sum())
        recall_pct = round(n_caught / n_whistleblower_flagged * 100.0, 3) if n_whistleblower_flagged > 0 else None

        validation_rows.append(
            {
                "ticker": ticker,
                "k": k,
                "n_field_flags": total_flagged,
                "n_decisive": total_decisive,
                "precision_pct": precision_pct,
                "n_whistleblower_flagged": n_whistleblower_flagged,
                "n_caught_by_mad": n_caught,
                "recall_pct": recall_pct,
            }
        )
    validation = pd.DataFrame(validation_rows)

    # --- E6b: independence check ---
    d_im_parts = []
    d_iy_parts = []
    d_my_parts = []
    for field_name in fields:
        ibkr_col = triple[f"{field_name}_ibkr"]
        massive_col = triple[f"{field_name}_massive"]
        yf_col = triple[f"{field_name}_yf"]
        d_im_parts.append(_pairwise_d(ibkr_col, massive_col))
        d_iy_parts.append(_pairwise_d(ibkr_col, yf_col))
        d_my_parts.append(_pairwise_d(massive_col, yf_col))

    pooled = pd.DataFrame(
        {
            "d_ibkr_massive": pd.concat(d_im_parts).reset_index(drop=True),
            "d_ibkr_yfinance": pd.concat(d_iy_parts).reset_index(drop=True),
            "d_massive_yfinance": pd.concat(d_my_parts).reset_index(drop=True),
        }
    )

    dispersions = {}
    for column_name in pooled.columns:
        values = pooled[column_name].to_numpy()
        stats = batch_stats(values.tolist())
        dispersions[column_name] = {
            "sigma": stddev_from_stats(stats),
            "conditional_mad_scaled": _conditional_mad_scaled(values),
        }

    correlation_matrix = pooled.corr()

    # Naive pooled correlation is misleading: on every (bar, field) where ibkr == massive exactly
    # (the dominant majority -- E1 showed 80-97% match rates), d_ibkr_yfinance and
    # d_massive_yfinance are IDENTICAL BY CONSTRUCTION (both computed from the same shared ibkr==
    # massive value), which trivially inflates their correlation to near 1.0 regardless of whether
    # yfinance shares any real upstream with either candidate. The genuinely informative test
    # restricts to (bar, field) instances where ibkr and massive actually disagree (d_ibkr_massive
    # != 0) -- only there can yfinance's behavior distinguish "tracks massive" from "independent".
    disagreement_only = pooled[pooled["d_ibkr_massive"] != 0]
    n_disagreement = int(len(disagreement_only))
    correlation_matrix_disagreement_only = disagreement_only.corr() if n_disagreement >= 10 else None

    im_mad = dispersions["d_ibkr_massive"]["conditional_mad_scaled"]
    iy_mad = dispersions["d_ibkr_yfinance"]["conditional_mad_scaled"]
    my_mad = dispersions["d_massive_yfinance"]["conditional_mad_scaled"]
    shares_upstream = False
    if im_mad > 0 and iy_mad > 0:
        tighter_than_im = my_mad <= im_mad * (1.0 - config.E6B_SHARED_UPSTREAM_MARGIN_PCT / 100.0)
        tighter_than_iy = my_mad <= iy_mad * (1.0 - config.E6B_SHARED_UPSTREAM_MARGIN_PCT / 100.0)
        shares_upstream = bool(tighter_than_im and tighter_than_iy)

    verdict = {
        "overlap_start": str(overlap_start.date()),
        "overlap_end": str(overlap_end.date()),
        "n_overlap_bars": int(len(triple)),
        "n_whistleblower_flagged": n_whistleblower_flagged,
        "typical_yf_deviation": typical_dev,
        "decisive_multiple": config.E6_DECISIVE_MULTIPLE,
        "conditional_mad_scaled_im": conditional_mad_scaled,
        "e6b_dispersions": dispersions,
        "e6b_correlations_pooled_naive": {
            "im_iy": round(float(correlation_matrix.loc["d_ibkr_massive", "d_ibkr_yfinance"]), 4),
            "im_my": round(float(correlation_matrix.loc["d_ibkr_massive", "d_massive_yfinance"]), 4),
            "iy_my": round(float(correlation_matrix.loc["d_ibkr_yfinance", "d_massive_yfinance"]), 4),
        },
        "n_disagreement_instances": n_disagreement,
        "e6b_correlations_disagreement_only": (
            {
                "im_iy": round(float(correlation_matrix_disagreement_only.loc["d_ibkr_massive", "d_ibkr_yfinance"]), 4),
                "im_my": round(float(correlation_matrix_disagreement_only.loc["d_ibkr_massive", "d_massive_yfinance"]), 4),
                "iy_my": round(float(correlation_matrix_disagreement_only.loc["d_ibkr_yfinance", "d_massive_yfinance"]), 4),
            }
            if correlation_matrix_disagreement_only is not None
            else None
        ),
        "e6b_shares_upstream": shares_upstream,
    }

    e6b = pooled.copy()
    e6b["ticker"] = ticker
    return validation, e6b, verdict


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_validation = []
    all_e6b = []
    ticker_verdicts = {}

    for ticker in config.TICKERS:
        validation, e6b, verdict = run_for_ticker(ticker)
        all_validation.append(validation)
        all_e6b.append(e6b)
        ticker_verdicts[ticker] = verdict

        print(f"\n=== {ticker} ===")
        print(f"Overlap window: {verdict['overlap_start']} .. {verdict['overlap_end']} ({verdict['n_overlap_bars']} triple-overlap bars)")
        print("*** BOUNDED PROXY, NOT GROUND TRUTH -- see module docstring's Framing note. ***")
        print(f"Whistleblower (yfinance-vs-ibkr, production tolerance, k={DEFAULT_RECONCILE_K}) flagged: {verdict['n_whistleblower_flagged']} bars")
        print(f"'Decisive' definition: yfinance's deviation from the farther candidate > {verdict['decisive_multiple']}x its own typical")
        print("deviation from the ibkr/massive midpoint, AND its deviation from the closer candidate <= that same typical.")
        print("Typical (median) yfinance deviation from midpoint, per field:")
        for field_name, value in verdict["typical_yf_deviation"].items():
            print(f"  {field_name}: {value:.8f}")

        print("\nPrecision / recall by k:")
        print(validation.to_string(index=False))

        print("\n--- E6b: independence check ---")
        print("Dispersion (conditional MAD, scaled) side by side:")
        for pair_name, stats in verdict["e6b_dispersions"].items():
            print(f"  {pair_name}: sigma={stats['sigma']:.8f}, conditional_mad_scaled={stats['conditional_mad_scaled']:.8f}")

        print("\nPairwise correlations, naive pooled (ALL bar/field instances, including ibkr==massive ties):")
        for pair_name, value in verdict["e6b_correlations_pooled_naive"].items():
            print(f"  {pair_name}: {value}")
        print("  ** MISLEADING: on every instance where ibkr==massive exactly, d_ibkr_yfinance and")
        print("  d_massive_yfinance are identical BY CONSTRUCTION -- this alone inflates iy_my toward 1.0")
        print("  regardless of any real shared upstream. Do not use this number for the independence verdict.")

        print(f"\nPairwise correlations, disagreement-only (ibkr != massive, n={verdict['n_disagreement_instances']}) -- the real test:")
        if verdict["e6b_correlations_disagreement_only"] is None:
            print("  too few disagreement instances to compute a meaningful correlation.")
        else:
            for pair_name, value in verdict["e6b_correlations_disagreement_only"].items():
                print(f"  {pair_name}: {value}")

        if verdict["e6b_shares_upstream"]:
            print(f"\nE6b verdict (dispersion test): massive-yfinance is >= {config.E6B_SHARED_UPSTREAM_MARGIN_PCT}% tighter than BOTH other pairs.")
            print("SUSPECT SHARED UPSTREAM -- weaken E6's precision/recall framing; yfinance may not be a fully independent third corner.")
        else:
            print("\nE6b verdict (dispersion test): massive-yfinance is NOT materially tighter than both other pairs on the naive dispersion measure.")
        print("See the disagreement-only correlation above for the more direct independence read; inspect both before trusting either alone.")

    validation_frame = pd.concat(all_validation, ignore_index=True)
    validation_path = RESULTS_DIR / "e6_validation.parquet"
    validation_frame.to_parquet(validation_path, index=False)

    e6b_frame = pd.concat(all_e6b, ignore_index=True)
    e6b_path = RESULTS_DIR / "e6b_independence.parquet"
    e6b_frame.to_parquet(e6b_path, index=False)

    manifest_data = manifest.load_manifest()
    manifest.save_manifest(
        manifest_data,
        EXPERIMENT_NAME,
        {
            "script": ".exp/validation/overlap_validation.py",
            "candidate_providers": list(config.CANDIDATE_PROVIDERS),
            "whistleblower_provider": config.WHISTLEBLOWER_PROVIDER,
            "decisive_multiple": config.E6_DECISIVE_MULTIPLE,
            "shared_upstream_margin_pct": config.E6B_SHARED_UPSTREAM_MARGIN_PCT,
            "production_k_used_for_recall": DEFAULT_RECONCILE_K,
            "tickers": config.TICKERS,
            "start_date": str(config.START_DATE),
            "end_date": str(config.END_DATE),
            "outputs": [str(validation_path), str(e6b_path)],
            "verdicts": ticker_verdicts,
        },
    )

    print(f"\nWrote {validation_path}")
    print(f"Wrote {e6b_path}")
    print(f"Updated {manifest.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
