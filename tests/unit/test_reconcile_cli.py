from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quant_data._internal.shared.postgres import (
    CandidatePairMadBandRow,
    DisagreementStatsRow,
    FieldGroupRow,
    FieldRow,
    IngestionCoverageRow,
    ProviderRow,
    StagingRow,
)
from quant_data._internal.shared.settings import ReconcileSettings, Settings
from reconcile import cli
from reconcile.algorithm import GRADUATION_THRESHOLD_MATCHED_BARS
from reconcile.cli import parse_args, run_reconciliation
from tests.mocks.reconcile_database import FakeReconcileDatabase

IBKR = 1
YFINANCE = 2
MASSIVE = 3
OHLC = 1
FIELD_OPEN = 1
FIELD_HIGH = 2
FIELD_LOW = 3
FIELD_CLOSE = 4

PROVIDERS = [
    ProviderRow(provider_id=IBKR, name="ibkr", role="candidate"),
    ProviderRow(provider_id=YFINANCE, name="yfinance", role="whistleblower"),
]
PROVIDERS_WITH_MASSIVE = PROVIDERS + [ProviderRow(provider_id=MASSIVE, name="massive", role="candidate")]
FIELD_GROUPS = [
    FieldGroupRow(field_group_id=OHLC, name="ohlc"),
]
FIELDS = [
    FieldRow(field_id=FIELD_OPEN, name="open"),
    FieldRow(field_id=FIELD_HIGH, name="high"),
    FieldRow(field_id=FIELD_LOW, name="low"),
    FieldRow(field_id=FIELD_CLOSE, name="close"),
]


def _seed_disagreement_stats(provider_id: int, ticker_id: int, sample_count: int, running_mean: float, running_m2: float) -> list[DisagreementStatsRow]:
    """One row per OHLC field -- provider_pair_disagreement is keyed (provider_id, ticker_id,
    field_id) since croicu/quant-data#28's slice 2; a single old-style seed used to cover the
    whole ohlc group, so tests reaching for "already has a working tolerance" now need all 4."""
    result: list[DisagreementStatsRow] = []
    for field in FIELDS:
        result.append(
            DisagreementStatsRow(
                provider_id=provider_id,
                ticker_id=ticker_id,
                field_id=field.field_id,
                sample_count=sample_count,
                running_mean=running_mean,
                running_m2=running_m2,
            )
        )
    return result


def _seed_candidate_pair_mad_band(ticker_id: int, conditional_mad_scaled: float, k: float) -> list[CandidatePairMadBandRow]:
    """One row per OHLC field -- candidate_pair_mad_band (tasks/retroactive_revision.md) is keyed
    (ticker_id, field_id), and resolve_automatic's _has_full_mad_band requires every field in the
    group to have a row before it engages at all."""
    result: list[CandidatePairMadBandRow] = []
    for field in FIELDS:
        result.append(CandidatePairMadBandRow(ticker_id=ticker_id, field_id=field.field_id, conditional_mad_scaled=conditional_mad_scaled, k=k))
    return result


def _settings(providers=None, preferred_provider="ibkr", k=3.0) -> Settings:
    return Settings(
        debug=False,
        providers=providers if providers is not None else ["yfinance", "ibkr"],
        reconcile=ReconcileSettings(preferred_provider=preferred_provider, k=k),
    )


def _staging_row(provider_id: int, ticker_id=1, date_id=10, time_id=20, timestamp=None, **overrides) -> StagingRow:
    defaults = dict(open=100.0, high=101.0, low=99.0, close=100.5, volume=1000, data_quality="accepted")
    defaults.update(overrides)
    return StagingRow(
        ticker_id=ticker_id,
        date_id=date_id,
        time_id=time_id,
        timestamp=timestamp if timestamp is not None else datetime(2026, 7, 24, 13, 30),
        provider_id=provider_id,
        **defaults,
    )


def _matched_bar_rows(count: int, ticker_id: int = 1, start_time_id: int = 100) -> list[StagingRow]:
    """`count` matched bars (both providers report identical, non-incomplete OHLC -- zero diff)
    for graduation-gate tests, which need far more bars than hand-writing each one individually."""
    result: list[StagingRow] = []
    for i in range(count):
        time_id = start_time_id + i
        timestamp = datetime(2026, 7, 24, 4, 0) + timedelta(minutes=i)
        result.append(_staging_row(IBKR, ticker_id=ticker_id, time_id=time_id, timestamp=timestamp))
        result.append(_staging_row(YFINANCE, ticker_id=ticker_id, time_id=time_id, timestamp=timestamp))
    return result


def _matched_bar_rows_with_massive(count: int, ticker_id: int = 1, start_time_id: int = 100) -> list[StagingRow]:
    """Same shape as _matched_bar_rows, but for a three-provider (ibkr, yfinance, massive) run --
    all three report identical, non-incomplete OHLC (zero diff), so agreement/graduation outcomes
    are fully predictable."""
    result: list[StagingRow] = []
    for i in range(count):
        time_id = start_time_id + i
        timestamp = datetime(2026, 7, 24, 4, 0) + timedelta(minutes=i)
        result.append(_staging_row(IBKR, ticker_id=ticker_id, time_id=time_id, timestamp=timestamp))
        result.append(_staging_row(YFINANCE, ticker_id=ticker_id, time_id=time_id, timestamp=timestamp))
        result.append(_staging_row(MASSIVE, ticker_id=ticker_id, time_id=time_id, timestamp=timestamp))
    return result


def test_agreement_promotes_bar_and_purges_staging():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.01, high=101.01, low=99.01, close=100.51, volume=1002),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1  # ohlc only -- volume rides along with whoever wins ohlc, not its own group
    assert stuck == 0
    # candidate's row purged; whistleblower's survives permanently exempt (croicu/quant-data#28)
    assert len(database.staging_rows) == 1
    assert database.staging_rows[0].provider_id == YFINANCE
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, _open_, _high, _low, _close, volume, _data_quality = fact_row
    assert volume == 1000  # IBKR's own volume, since IBKR won ohlc
    for _, _, _, _, _, resolution_path in database.fact_reconciliation:
        assert resolution_path == "agreement"


def test_trade_group_rides_along_with_ohlc_winner_not_the_losing_reporter():
    # croicu/quant-data#61: wap/trade_count are winner-gated, same precedent already set for
    # volume -- promoted from whichever provider won this bar's OHLC vote, even when a losing
    # candidate also reported them ("no data over bad data").
    staging_rows = [
        _staging_row(IBKR, data_quality="incomplete", wap=999.0, trade_count=1),
        _staging_row(MASSIVE, data_quality="accepted", wap=500.25, trade_count=300),
        _staging_row(YFINANCE, data_quality="incomplete"),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS_WITH_MASSIVE, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"]), finalize=False)

    assert resolved == 1
    assert stuck == 0
    for _, _, _, _, winning_provider_id, resolution_path in database.fact_reconciliation:
        assert winning_provider_id == MASSIVE
        assert resolution_path == "completeness"
    wap, trade_count, avg_bid, avg_ask, midpoint_open, midpoint_high, midpoint_low, midpoint_close = database.fact_market_data_supplement[(1, 10, 20)]
    # Massive's own report, since massive won ohlc -- not ibkr's 999.0/1, even though ibkr
    # reported wap/trade_count too.
    assert (wap, trade_count) == (500.25, 300)


def test_quote_group_promotes_regardless_of_which_provider_won_ohlc():
    # croicu/quant-data#61: avg_bid/avg_ask/midpoint_* are NOT winner-gated -- promoted from
    # whichever staging row reports them, even when that provider lost the OHLC vote.
    staging_rows = [
        _staging_row(IBKR, data_quality="incomplete", avg_bid=100.0, avg_ask=100.1),
        _staging_row(MASSIVE, data_quality="accepted"),
        _staging_row(YFINANCE, data_quality="incomplete"),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS_WITH_MASSIVE, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"]), finalize=False)

    assert resolved == 1
    assert stuck == 0
    for _, _, _, _, winning_provider_id, _resolution_path in database.fact_reconciliation:
        assert winning_provider_id == MASSIVE  # massive won ohlc...
    wap, trade_count, avg_bid, avg_ask, midpoint_open, midpoint_high, midpoint_low, midpoint_close = database.fact_market_data_supplement[(1, 10, 20)]
    assert (avg_bid, avg_ask) == (100.0, 100.1)  # ...but avg_bid/avg_ask still comes from ibkr, who lost


def test_supplement_fields_are_none_when_nobody_reports_them():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.01, high=101.01, low=99.01, close=100.51, volume=1002),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1
    assert database.fact_market_data_supplement[(1, 10, 20)] == (None, None, None, None, None, None, None, None)


def test_completeness_resolves_yahoo_premarket_gap():
    # The actual motivating case: Yahoo (whistleblower) reports incomplete (premarket zero
    # placeholder), IBKR (candidate) has real data -- both groups should resolve via
    # completeness, promoting IBKR's values. Seeded as already-graduated (croicu/quant-data#28's
    # per-ticker gate) so this test stays focused on Tier 1 completeness mechanics, not graduation.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500, data_quality="accepted"),
        _staging_row(YFINANCE, open=0.0, high=0.0, low=0.0, close=0.0, volume=0, data_quality="incomplete"),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1
    assert stuck == 0
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, volume, _data_quality = fact_row
    assert (open_, high, low, close, volume) == (100.0, 101.0, 99.0, 100.5, 500)
    for _, _, _, _, _, resolution_path in database.fact_reconciliation:
        assert resolution_path == "completeness"


def _quiet_yfinance_window(target_time_id: int, diff_back: float, diff_fwd: float, half_span: int = 15) -> list[StagingRow]:
    """A wide (2*half_span+1 bar) background of yfinance staging rows around target_time_id, with
    an alternating 0.01/0.02 close-step (so the MAD-based reference scale used by
    reconcile/outlier_detection.py is a genuine small positive number, matching
    tests/unit/test_outlier_detection.py's _quiet_window) and the target's own back/forward diffs
    overridden to diff_back/diff_fwd. reconcile/cli.py's outlier pass now builds a
    +/-BACKGROUND_HALF_WINDOW_MINUTES window, so this needs to be at least that wide to actually
    exercise real background data rather than the insufficient-sample skip."""
    base_timestamp = datetime(2026, 7, 24, 14, 0)
    values: dict[int, float] = {}
    value = 100.0
    step_toggle = True
    for time_id in range(target_time_id - half_span, target_time_id):
        values[time_id] = value
        value += 0.01 if step_toggle else 0.02
        step_toggle = not step_toggle
    back_one_value = values[target_time_id - 1]
    target_value = back_one_value + diff_back
    values[target_time_id] = target_value
    fwd_one_value = target_value + diff_fwd
    values[target_time_id + 1] = fwd_one_value
    value = fwd_one_value
    step_toggle = True
    for time_id in range(target_time_id + 2, target_time_id + half_span + 1):
        value += 0.01 if step_toggle else 0.02
        step_toggle = not step_toggle
        values[time_id] = value

    rows: list[StagingRow] = []
    for time_id, close in values.items():
        timestamp = base_timestamp + timedelta(minutes=(time_id - target_time_id))
        rows.append(_staging_row(YFINANCE, time_id=time_id, timestamp=timestamp, close=close))
    return rows


def test_outlier_detection_rejects_whistleblower_bar_and_candidate_still_promotes():
    # A yfinance reversal spike at time_id=102 (same shape/magnitude validated in
    # tests/unit/test_outlier_detection.py's test_reversal_spike_is_rejected) should get marked
    # rejected by the outlier pass, then -- since rejected is treated exactly like incomplete --
    # Tier 1 should auto-promote ibkr's own clean value for that bar in the very same run.
    base_timestamp = datetime(2026, 7, 24, 14, 0)
    staging_rows = _quiet_yfinance_window(target_time_id=102, diff_back=3.0, diff_fwd=-3.0)
    staging_rows.append(
        _staging_row(IBKR, time_id=102, timestamp=base_timestamp + timedelta(minutes=2), open=50.0, high=51.0, low=49.0, close=50.5, volume=500)
    )
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    yfinance_row_at_102 = None
    for row in database.staging_rows:
        if row.provider_id == YFINANCE and row.time_id == 102:
            yfinance_row_at_102 = row
    assert yfinance_row_at_102 is not None
    assert yfinance_row_at_102.data_quality == "rejected"

    # ibkr's own clean value promoted via Tier 1 completeness (rejected treated like incomplete).
    fact_row = database.fact_market_data[(1, 10, 102)]
    _timestamp, open_, high, low, close, volume, _data_quality = fact_row
    assert (open_, high, low, close, volume) == (50.0, 51.0, 49.0, 50.5, 500)
    resolution_paths_for_102 = []
    for ticker_id, date_id, time_id, _field_group_id, _winning_provider_id, resolution_path in database.fact_reconciliation:
        if time_id == 102:
            resolution_paths_for_102.append(resolution_path)
    assert resolution_paths_for_102 == ["completeness"]


def test_outlier_detection_leaves_normal_bars_accepted():
    staging_rows = [
        _staging_row(YFINANCE, time_id=100, timestamp=datetime(2026, 7, 24, 14, 0), close=100.00),
        _staging_row(YFINANCE, time_id=101, timestamp=datetime(2026, 7, 24, 14, 1), close=100.01),
        _staging_row(YFINANCE, time_id=102, timestamp=datetime(2026, 7, 24, 14, 2), close=100.02),
        _staging_row(YFINANCE, time_id=103, timestamp=datetime(2026, 7, 24, 14, 3), close=100.03),
        _staging_row(YFINANCE, time_id=104, timestamp=datetime(2026, 7, 24, 14, 4), close=100.04),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)

    run_reconciliation(database, _settings(), finalize=False)

    for row in database.staging_rows:
        assert row.data_quality == "accepted"


def test_bar_missing_a_configured_provider_is_left_alone():
    staging_rows = [_staging_row(IBKR)]  # yfinance never reported for this bar

    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_reconciliation == []
    assert len(database.staging_rows) == 1  # untouched


def test_candidate_promotes_when_whistleblower_confirmed_absent():
    # yfinance never wrote a row for this bar at all -- but ingestion_coverage confirms its date
    # range was actually ingested, so the absence is treated as confirmed, not "not ingested yet"
    # (croicu/quant-data#31). Ticker pre-seeded as graduated so this test stays focused on the
    # whistleblower-absence mechanics, not the graduation gate.
    staging_rows = [_staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500)]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    ingestion_coverage = [IngestionCoverageRow(ticker_id=1, provider_id=YFINANCE, start_date_id=5, end_date_id=15)]
    database = FakeReconcileDatabase(
        PROVIDERS,
        FIELD_GROUPS,
        staging_rows,
        fields=FIELDS,
        disagreement_stats=disagreement_stats,
        ingestion_coverage=ingestion_coverage,
    )

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1
    assert stuck == 0
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, volume, _data_quality = fact_row
    assert (open_, high, low, close, volume) == (100.0, 101.0, 99.0, 100.5, 500)
    for _, _, _, _, _, resolution_path in database.fact_reconciliation:
        assert resolution_path == "completeness"


def test_candidate_stays_stuck_when_whistleblower_not_yet_ingested():
    # yfinance never wrote a row for this bar, and ingestion_coverage has no range covering this
    # date at all -- too early to conclude anything, so the bar must be left alone exactly like a
    # bar missing any other required provider: not evaluated, and critically not marked pending
    # (croicu/quant-data#31 -- a premature "stuck" flag would be a strong, wrong conclusion here).
    staging_rows = [_staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500)]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(
        PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats
    )  # no ingestion_coverage at all

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_reconciliation == []
    assert len(database.staging_rows) == 1  # untouched
    assert database.pending_manual_resolution == set()


def test_large_disagreement_stays_stuck_until_finalize():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 1
    assert database.fact_reconciliation == []
    assert (1, 10, 20) not in database.fact_market_data
    assert len(database.staging_rows) == 2  # still stuck, not purged

    resolved, stuck = run_reconciliation(database, _settings(), finalize=True)

    assert resolved == 1  # the stuck ohlc group, now finalized
    assert stuck == 0
    assert (1, 10, 20) in database.fact_market_data
    # candidate's row purged; whistleblower's survives permanently exempt (croicu/quant-data#28)
    assert len(database.staging_rows) == 1
    assert database.staging_rows[0].provider_id == YFINANCE


def test_finalize_promotes_preferred_providers_raw_value():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5, volume=1000),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    # --finalize only ever touches the pending-manual-resolution queue -- a plain pass must run
    # first to actually mark this bar pending before --finalize has anything to do.
    run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=False)
    run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=True)

    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, _volume, _data_quality = fact_row
    assert (open_, high, low, close) == (100.0, 101.0, 99.0, 100.5)  # IBKR's raw value, not Yahoo's


def test_agreement_updates_disagreement_stats():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    run_reconciliation(database, _settings(), finalize=False)

    # Each field gets its own independent update now (croicu/quant-data#28) -- 100 seeded + 1
    # observation per field, not one shared bucket across all 4.
    for field_id in (FIELD_OPEN, FIELD_HIGH, FIELD_LOW, FIELD_CLOSE):
        updated_field_stats = database.disagreement_stats[(IBKR, 1, field_id)]
        assert updated_field_stats.sample_count == 101


def test_ungraduated_ticker_stays_completely_unevaluated():
    # One bar short of graduation, plus an obviously Tier-1-eligible bar mixed in -- nothing
    # resolves for either, and no stats get computed, since an ungraduated ticker gets no Tier 1-4
    # attempt at all (croicu/quant-data#28).
    staging_rows = _matched_bar_rows(GRADUATION_THRESHOLD_MATCHED_BARS - 1)
    staging_rows.append(_staging_row(IBKR, time_id=9000, timestamp=datetime(2026, 7, 24, 20, 0), data_quality="accepted"))
    staging_rows.append(
        _staging_row(YFINANCE, time_id=9000, timestamp=datetime(2026, 7, 24, 20, 0), open=0.0, high=0.0, low=0.0, close=0.0, data_quality="incomplete")
    )
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 0
    assert database.disagreement_stats == {}
    assert database.fact_reconciliation == []
    assert len(database.staging_rows) == len(staging_rows)  # completely untouched


def test_ticker_graduates_at_threshold_and_resolves_same_run():
    # Exactly at the threshold, plus a Tier-1-eligible bar mixed in -- graduation triggers, and
    # the ticker's ENTIRE currently-fetched backlog (matched and unmatched together) resolves in
    # this same run, not just the batch used to compute the tolerance.
    staging_rows = _matched_bar_rows(GRADUATION_THRESHOLD_MATCHED_BARS)
    staging_rows.append(_staging_row(IBKR, time_id=9000, timestamp=datetime(2026, 7, 24, 20, 0), data_quality="accepted"))
    staging_rows.append(
        _staging_row(YFINANCE, time_id=9000, timestamp=datetime(2026, 7, 24, 20, 0), open=0.0, high=0.0, low=0.0, close=0.0, data_quality="incomplete")
    )
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == GRADUATION_THRESHOLD_MATCHED_BARS + 1  # every matched bar (agreement) + the Tier-1 bar (completeness)
    assert stuck == 0
    for field_id in (FIELD_OPEN, FIELD_HIGH, FIELD_LOW, FIELD_CLOSE):
        graduated_stats = database.disagreement_stats[(IBKR, 1, field_id)]
        assert graduated_stats.sample_count >= GRADUATION_THRESHOLD_MATCHED_BARS  # batch + post-graduation Tier 2 updates
        assert graduated_stats.running_mean == 0.0  # every matched bar agreed exactly -- zero diff throughout


def test_already_graduated_ticker_does_not_re_trigger_graduation():
    # Regression guard: a ticker with pre-existing stats must not have its graduation batch
    # recomputed just because a plain pass runs again -- "has stats" alone is graduation.
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=100.0, high=101.0, low=99.0, close=100.5),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1
    assert stuck == 0
    assert len(database.disagreement_stats) == 4  # unchanged in shape -- just the normal Tier 2 update, not a batch recompute


def test_two_ticker_isolation_graduated_and_ungraduated_in_same_run():
    ticker_a = 1
    ticker_b = 2
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=ticker_a, sample_count=100, running_mean=0.0, running_m2=0.000064)
    staging_rows = [
        _staging_row(IBKR, ticker_id=ticker_a, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, ticker_id=ticker_a, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(IBKR, ticker_id=ticker_b, time_id=21, timestamp=datetime(2026, 7, 24, 13, 31)),
        _staging_row(YFINANCE, ticker_id=ticker_b, time_id=21, timestamp=datetime(2026, 7, 24, 13, 31)),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1  # ticker_a's bar only
    assert stuck == 0
    assert len(database.disagreement_stats) == 4  # still just ticker_a's 4 fields, ticker_b never graduated
    remaining = set()
    for row in database.staging_rows:
        remaining.add((row.ticker_id, row.provider_id))
    # ticker_a's candidate purged (whistleblower exempt); ticker_b entirely untouched
    assert remaining == {(ticker_a, YFINANCE), (ticker_b, IBKR), (ticker_b, YFINANCE)}


def test_seeding_lag_converges_within_a_single_run():
    # bar_early is listed FIRST (so it's visited first in every pass) and disagrees just outside
    # the seeded tolerance (3 * 0.0008 * 100 = 0.24) -- under the old single-pass design this
    # would stay stuck for the whole run. bar_widen_1..9, listed AFTER it, each agree well inside
    # the seed tolerance and each update provider_pair_disagreement on resolution -- each field's
    # own bucket gets exactly one relative-diff update per resolved bar (croicu/quant-data#28's
    # per-field re-key, not four identical updates pooled into one shared bucket as before), which
    # is why this needs 9 widening bars, not 5, to pull the measured stddev up enough (from ~0.0008
    # to ~0.00094, tolerance from 0.24 to ~0.283) that bar_early's 0.28 diff falls back in range --
    # but only on a second pass over the still-unresolved bars, since bar_early was already
    # evaluated (and failed) earlier in the very same first pass.
    staging_rows = [
        _staging_row(IBKR, time_id=20, timestamp=datetime(2026, 7, 24, 13, 30), open=100.0, high=100.0, low=100.0, close=100.0),
        _staging_row(YFINANCE, time_id=20, timestamp=datetime(2026, 7, 24, 13, 30), open=99.72, high=99.72, low=99.72, close=99.72),
    ]
    for i in range(9):
        time_id = 30 + i
        timestamp = datetime(2026, 7, 24, 13, 40 + i)
        staging_rows.append(_staging_row(IBKR, time_id=time_id, timestamp=timestamp, open=100.0, high=100.0, low=100.0, close=100.0))
        staging_rows.append(_staging_row(YFINANCE, time_id=time_id, timestamp=timestamp, open=99.8, high=99.8, low=99.8, close=99.8))

    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 10  # bar_early + the 9 widening bars, all within one run_reconciliation() call
    assert stuck == 0
    assert (1, 10, 20) in database.fact_market_data  # bar_early did resolve, just not on the first attempt


def test_purge_is_deferred_while_an_adjacent_bar_is_still_stuck():
    # bar_a (10:00) and bar_b (10:01, its t+1 neighbor) are one minute apart. bar_a agrees and
    # resolves immediately; bar_b disagrees far beyond tolerance and stays stuck. Even though
    # bar_a is fully resolved and promoted, its staging rows must stick around -- a future run's
    # Tier-3 boundary-fix check for bar_b still needs bar_a as a windowed neighbor, and purging
    # immediately would lose that data permanently (tasks/quant_reconcile.md's "missing neighbor"
    # gap).
    staging_rows = [
        _staging_row(IBKR, time_id=20, timestamp=datetime(2026, 7, 24, 10, 0), open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, time_id=20, timestamp=datetime(2026, 7, 24, 10, 0), open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(IBKR, time_id=21, timestamp=datetime(2026, 7, 24, 10, 1), open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, time_id=21, timestamp=datetime(2026, 7, 24, 10, 1), open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1  # bar_a
    assert stuck == 1  # bar_b, newly marked pending manual resolution
    assert (1, 10, 20) in database.fact_market_data  # bar_a promoted...
    assert len(database.staging_rows) == 4  # ...but nothing purged yet -- bar_b still needs it

    # --finalize only fetches the pending queue (bar_b) -- not bar_a, which was never pending.
    # bar_b resolves and purges; bar_a's own purge-eligibility isn't re-checked this run (it isn't
    # fetched at all), so its rows linger a little longer than strictly necessary. A known,
    # accepted tradeoff of --finalize's narrow scope (tasks/quant_reconcile.md) -- self-heals on
    # the very next plain run, which re-fetches bar_a (already resolved, so no Tier 1-3 re-attempt)
    # and re-checks its now-unblocked neighbor.
    resolved, stuck = run_reconciliation(database, _settings(), finalize=True)

    assert resolved == 1  # bar_b, via finalize
    assert stuck == 0
    assert (1, 10, 21) in database.fact_market_data
    # bar_a's rows still linger (see comment above) -- both of bar_a's rows, plus bar_b's own
    # whistleblower row surviving its candidate's purge (croicu/quant-data#28's permanent exemption)
    assert len(database.staging_rows) == 3
    remaining = set()
    for row in database.staging_rows:
        remaining.add((row.time_id, row.provider_id))
    assert remaining == {(20, IBKR), (20, YFINANCE), (21, YFINANCE)}

    # Next plain run: bar_a is already resolved (no Tier 1-3 re-attempt), and its neighbor (bar_b)
    # is gone (its orphaned whistleblower-only row can never satisfy the every-provider-reported
    # check again), so bar_a finally purges too -- leaving only the two orphaned whistleblower rows.
    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0  # bar_a was already resolved, nothing new to resolve
    assert stuck == 0
    assert len(database.staging_rows) == 2
    remaining = set()
    for row in database.staging_rows:
        remaining.add((row.time_id, row.provider_id))
    assert remaining == {(20, YFINANCE), (21, YFINANCE)}


def test_plain_pass_skips_an_already_pending_bar():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)
    database.mark_pending_manual_resolution(1, 10, 20, OHLC, datetime(2026, 7, 24, 13, 30))  # simulates an earlier run's flag

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 0  # not newly marked pending -- it was never even fetched this run
    assert database.fact_reconciliation == []  # no Tier 1-3 attempt was made
    assert len(database.staging_rows) == 2  # untouched


def test_finalize_alone_with_nothing_pending_does_nothing():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, fields=FIELDS)

    # No plain pass ran first, so nothing is pending -- --finalize alone has nothing to do.
    resolved, stuck = run_reconciliation(database, _settings(), finalize=True)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_market_data == {}
    assert len(database.staging_rows) == 2  # untouched


def test_two_candidates_confirmed_absent_whistleblower_resolves_unadjudicated_and_skips_welford():
    # Regression tests 2+3 (croicu/quant-data#44): two already-graduated candidates agree with
    # each other; the whistleblower is confirmed absent (ingestion_coverage covers the date, no
    # staging row at all). Must resolve automatically via the new unadjudicated fallback -- not
    # get stuck the way an un-gated Tier 2 comparison against a synthetic placeholder would -- and
    # must NOT feed provider_pair_disagreement's Welford variance, since no real agreement was
    # ever observed.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500),
        _staging_row(MASSIVE, open=100.0, high=101.0, low=99.0, close=100.5, volume=500),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    disagreement_stats += _seed_disagreement_stats(MASSIVE, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    ingestion_coverage = [IngestionCoverageRow(ticker_id=1, provider_id=YFINANCE, start_date_id=5, end_date_id=15)]
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows,
        fields=FIELDS,
        disagreement_stats=disagreement_stats,
        ingestion_coverage=ingestion_coverage,
    )
    stats_before = dict(database.disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"], preferred_provider="ibkr"), finalize=False)

    assert resolved == 1
    assert stuck == 0
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, volume, _data_quality = fact_row
    assert (open_, high, low, close, volume) == (100.0, 101.0, 99.0, 100.5, 500)
    for _, _, _, _, winning_provider_id, resolution_path in database.fact_reconciliation:
        assert resolution_path == "unadjudicated"
        assert winning_provider_id == IBKR
    assert database.disagreement_stats == stats_before  # Welford untouched


def test_historical_mad_band_promotes_via_new_resolution_path_instead_of_unadjudicated():
    # tasks/retroactive_revision.md: same shape as the unadjudicated test above (confirmed-absent
    # whistleblower, two already-graduated candidates agreeing), but this ticker also has a seeded
    # candidate_pair_mad_band -- must resolve via 'historical_mad_agreement', not 'unadjudicated',
    # and the fetch/field_id-to-field_name wiring in cli.py must actually engage it end-to-end.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500),
        _staging_row(MASSIVE, open=100.0, high=101.0, low=99.0, close=100.5, volume=500),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    disagreement_stats += _seed_disagreement_stats(MASSIVE, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    ingestion_coverage = [IngestionCoverageRow(ticker_id=1, provider_id=YFINANCE, start_date_id=5, end_date_id=15)]
    mad_bands = _seed_candidate_pair_mad_band(ticker_id=1, conditional_mad_scaled=1e-5, k=3.0)
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows,
        fields=FIELDS,
        disagreement_stats=disagreement_stats,
        ingestion_coverage=ingestion_coverage,
        candidate_pair_mad_bands=mad_bands,
    )

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"], preferred_provider="ibkr"), finalize=False)

    assert resolved == 1
    assert stuck == 0
    for _, _, _, _, winning_provider_id, resolution_path in database.fact_reconciliation:
        assert resolution_path == "historical_mad_agreement"
        assert winning_provider_id == IBKR


def test_historical_mad_band_leaves_bar_pending_when_candidates_disagree_beyond_band():
    # Same setup, but the two candidates disagree by more than the seeded band allows -- must NOT
    # fall back to unadjudicated's blind promotion; stays stuck for --finalize/manual review.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500),
        _staging_row(MASSIVE, open=101.0, high=102.0, low=100.0, close=101.5, volume=500),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    disagreement_stats += _seed_disagreement_stats(MASSIVE, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    ingestion_coverage = [IngestionCoverageRow(ticker_id=1, provider_id=YFINANCE, start_date_id=5, end_date_id=15)]
    mad_bands = _seed_candidate_pair_mad_band(ticker_id=1, conditional_mad_scaled=1e-5, k=3.0)
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows,
        fields=FIELDS,
        disagreement_stats=disagreement_stats,
        ingestion_coverage=ingestion_coverage,
        candidate_pair_mad_bands=mad_bands,
    )

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"], preferred_provider="ibkr"), finalize=False)

    assert resolved == 0
    assert stuck == 1
    assert database.fact_reconciliation == []
    assert (1, 10, 20, OHLC) in database.pending_manual_resolution


def test_reevaluate_unadjudicated_relabels_agreement_without_touching_the_fact_row():
    # tasks/reevaluate_unadjudicated_bars.md: an already-promoted 'unadjudicated' bar, now
    # re-checked against a since-seeded band -- confirmed agreement relabels resolution_path but
    # leaves winning_provider_id and fact_market_data_1min completely untouched.
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows=[],
        fields=FIELDS,
        candidate_pair_mad_bands=_seed_candidate_pair_mad_band(ticker_id=1, conditional_mad_scaled=1e-5, k=3.0),
        market_data_archive=[
            (1, 10, 20, IBKR, 100.0, 101.0, 99.0, 100.5),
            (1, 10, 20, MASSIVE, 100.001, 101.001, 99.001, 100.501),
        ],
    )
    database.fact_reconciliation.append((1, 10, 20, OHLC, IBKR, "unadjudicated"))
    fact_before = dict(database.fact_market_data)

    agreed, disputed = run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=False, reevaluate_unadjudicated_bars=True)

    assert agreed == 1
    assert disputed == 0
    assert database.fact_reconciliation == [(1, 10, 20, OHLC, IBKR, "historical_mad_agreement")]
    assert database.fact_market_data == fact_before  # untouched, no retraction/rewrite


def test_reevaluate_unadjudicated_flags_disagreement_without_touching_the_fact_row():
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows=[],
        fields=FIELDS,
        candidate_pair_mad_bands=_seed_candidate_pair_mad_band(ticker_id=1, conditional_mad_scaled=1e-5, k=3.0),
        market_data_archive=[
            (1, 10, 20, IBKR, 100.0, 101.0, 99.0, 100.5),
            (1, 10, 20, MASSIVE, 101.0, 102.0, 100.0, 101.5),
        ],
    )
    database.fact_reconciliation.append((1, 10, 20, OHLC, IBKR, "unadjudicated"))
    fact_before = dict(database.fact_market_data)

    agreed, disputed = run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=False, reevaluate_unadjudicated_bars=True)

    assert agreed == 0
    assert disputed == 1
    assert database.fact_reconciliation == [(1, 10, 20, OHLC, IBKR, "unadjudicated_disputed")]
    assert database.fact_market_data == fact_before  # untouched -- no retraction mechanism exists


def test_reevaluate_unadjudicated_leaves_unseeded_ticker_untouched():
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows=[],
        fields=FIELDS,
        market_data_archive=[
            (1, 10, 20, IBKR, 100.0, 101.0, 99.0, 100.5),
            (1, 10, 20, MASSIVE, 100.001, 101.001, 99.001, 100.501),
        ],
    )
    database.fact_reconciliation.append((1, 10, 20, OHLC, IBKR, "unadjudicated"))

    agreed, disputed = run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=False, reevaluate_unadjudicated_bars=True)

    assert agreed == 0
    assert disputed == 0
    assert database.fact_reconciliation == [(1, 10, 20, OHLC, IBKR, "unadjudicated")]


def test_second_candidate_graduates_on_already_graduated_ticker():
    # Regression test 6 (croicu/quant-data#44): ibkr is already graduated on ticker 1 with mature,
    # Welford-accumulated stats. massive joins as a second candidate and accumulates enough
    # matched bars (all three providers agreeing) to graduate on its own in this run --
    # preferredProvider is set to massive so ibkr never wins a Tier 2 tiebreak here, isolating the
    # assertion to the graduation *batch* itself: ibkr's stats must come out byte-for-byte
    # unchanged, not silently recomputed just because ibkr is present in the same matched bars
    # massive's graduation batch scans (the bug the old per-ticker-only gate had).
    ibkr_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=5000, running_mean=0.0001, running_m2=0.02)
    staging_rows = _matched_bar_rows_with_massive(GRADUATION_THRESHOLD_MATCHED_BARS)
    database = FakeReconcileDatabase(PROVIDERS_WITH_MASSIVE, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=ibkr_stats)
    ibkr_stats_before = {}
    for field in FIELDS:
        ibkr_stats_before[field.field_id] = database.disagreement_stats[(IBKR, 1, field.field_id)]

    run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"], preferred_provider="massive"), finalize=False)

    for field in FIELDS:
        assert database.disagreement_stats[(IBKR, 1, field.field_id)] == ibkr_stats_before[field.field_id]
    assert database.disagreement_stats[(MASSIVE, 1, FIELD_OPEN)].sample_count >= GRADUATION_THRESHOLD_MATCHED_BARS


def test_second_candidate_graduates_missing_field_without_touching_already_graduated_fields():
    # Regression test 5 (croicu/quant-data#44), asserted at the tolerance lookup key's own
    # granularity: massive already has mature stats for open/high/low but is missing close -- the
    # only way to exercise per-field graduation today, since a normal graduation batch always
    # computes all 4 fields atomically. The fix must fill in exactly the missing key without
    # disturbing the three already-present ones. preferredProvider is ibkr so massive never wins a
    # Tier 2 tiebreak, isolating the assertion to the graduation batch itself.
    ibkr_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=5000, running_mean=0.0, running_m2=0.02)
    massive_partial_stats = [
        DisagreementStatsRow(provider_id=MASSIVE, ticker_id=1, field_id=FIELD_OPEN, sample_count=5000, running_mean=0.0, running_m2=0.02),
        DisagreementStatsRow(provider_id=MASSIVE, ticker_id=1, field_id=FIELD_HIGH, sample_count=5000, running_mean=0.0, running_m2=0.02),
        DisagreementStatsRow(provider_id=MASSIVE, ticker_id=1, field_id=FIELD_LOW, sample_count=5000, running_mean=0.0, running_m2=0.02),
        # FIELD_CLOSE deliberately absent -- massive's own graduation gap.
    ]
    staging_rows = _matched_bar_rows_with_massive(GRADUATION_THRESHOLD_MATCHED_BARS)
    database = FakeReconcileDatabase(PROVIDERS_WITH_MASSIVE, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=ibkr_stats + massive_partial_stats)
    massive_open_before = database.disagreement_stats[(MASSIVE, 1, FIELD_OPEN)]
    massive_high_before = database.disagreement_stats[(MASSIVE, 1, FIELD_HIGH)]
    massive_low_before = database.disagreement_stats[(MASSIVE, 1, FIELD_LOW)]
    assert (MASSIVE, 1, FIELD_CLOSE) not in database.disagreement_stats

    run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"], preferred_provider="ibkr"), finalize=False)

    assert database.disagreement_stats[(MASSIVE, 1, FIELD_OPEN)] == massive_open_before
    assert database.disagreement_stats[(MASSIVE, 1, FIELD_HIGH)] == massive_high_before
    assert database.disagreement_stats[(MASSIVE, 1, FIELD_LOW)] == massive_low_before
    assert database.disagreement_stats[(MASSIVE, 1, FIELD_CLOSE)].sample_count >= GRADUATION_THRESHOLD_MATCHED_BARS


def test_candidate_resolves_using_remaining_candidate_when_other_candidate_confirmed_absent():
    # croicu/quant-data#49: massive never reported for this bar at all, but ingestion_coverage
    # confirms its date range was actually ingested for this ticker -- a real "nothing here," not
    # "not ingested yet." ibkr alone (subject to whistleblower validation) must still resolve the
    # bar via ordinary Tier 2 agreement, exactly the "choose the candidate with data" behavior --
    # algorithm.py needs no special-casing since it never assumed a fixed candidate count.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.01, high=101.01, low=99.01, close=100.51, volume=1002),
        # no massive row for this bar at all
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    ingestion_coverage = [IngestionCoverageRow(ticker_id=1, provider_id=MASSIVE, start_date_id=5, end_date_id=15)]
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE,
        FIELD_GROUPS,
        staging_rows,
        fields=FIELDS,
        disagreement_stats=disagreement_stats,
        ingestion_coverage=ingestion_coverage,
    )

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"]), finalize=False)

    assert resolved == 1
    assert stuck == 0
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, volume, _data_quality = fact_row
    assert (open_, high, low, close, volume) == (100.0, 101.0, 99.0, 100.5, 1000)  # ibkr's own values
    for _, _, _, _, winning_provider_id, resolution_path in database.fact_reconciliation:
        assert winning_provider_id == IBKR
        assert resolution_path == "agreement"


def test_candidate_stays_untouched_when_other_candidate_not_yet_confirmed_absent():
    # Same shape as above, but no ingestion_coverage row for massive at all -- too early to
    # conclude anything, so the bar must be left alone exactly like a bar missing any other
    # required provider: not evaluated, and critically not marked pending.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.01, high=101.01, low=99.01, close=100.51, volume=1002),
    ]
    disagreement_stats = _seed_disagreement_stats(IBKR, ticker_id=1, sample_count=100, running_mean=0.0, running_m2=0.000064)
    database = FakeReconcileDatabase(
        PROVIDERS_WITH_MASSIVE, FIELD_GROUPS, staging_rows, fields=FIELDS, disagreement_stats=disagreement_stats
    )  # no ingestion_coverage at all

    resolved, stuck = run_reconciliation(database, _settings(providers=["yfinance", "ibkr", "massive"]), finalize=False)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_reconciliation == []
    assert len(database.staging_rows) == 2  # untouched
    assert database.pending_manual_resolution == set()


def test_parse_args_defaults_finalize_to_false():
    arguments = parse_args([])

    assert arguments.finalize is False


def test_parse_args_recognizes_finalize_flag():
    arguments = parse_args(["--finalize"])

    assert arguments.finalize is True


def test_parse_args_recognizes_reevaluate_unadjudicated_flag():
    arguments = parse_args(["--reevaluate-unadjudicated"])

    assert arguments.reevaluate_unadjudicated is True
    assert arguments.finalize is False


def test_parse_args_rejects_finalize_and_reevaluate_unadjudicated_together():
    with pytest.raises(SystemExit):
        parse_args(["--finalize", "--reevaluate-unadjudicated"])


def _write_settings(tmp_path: Path, **overrides) -> Path:
    settings_path = tmp_path / "settings.json"
    payload = {
        "debug": False,
        "logLevel": "error",
        "postgres": {"host": "localhost", "port": 5433, "user": "test", "password": "test", "dbname": "test"},
        "providers": ["yfinance", "ibkr"],
    }
    payload.update(overrides)
    settings_path.write_text(json.dumps({"settings": payload}), encoding="utf-8")
    return settings_path


def test_main_returns_one_when_postgres_not_configured(tmp_path):
    settings_path = _write_settings(tmp_path, postgres=None)

    exit_code = cli.main([], settings_path=settings_path)

    assert exit_code == 1


def test_main_closes_database_and_returns_zero_on_success(tmp_path):
    settings_path = _write_settings(tmp_path)
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, [])

    def factory(_postgres_settings):
        return database

    exit_code = cli.main([], settings_path=settings_path, database_factory=factory)

    assert exit_code == 0
    assert database.closed is True
