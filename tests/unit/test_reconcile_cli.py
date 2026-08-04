from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from quant_data._internal.shared.postgres import DisagreementStatsRow, FieldGroupRow, ProviderRow, StagingRow
from quant_data._internal.shared.settings import ReconcileSettings, Settings
from reconcile import cli
from reconcile.cli import parse_args, run_reconciliation
from tests.mocks.reconcile_database import FakeReconcileDatabase

IBKR = 1
YFINANCE = 2
OHLC = 1

PROVIDERS = [
    ProviderRow(provider_id=IBKR, name="ibkr", role="candidate"),
    ProviderRow(provider_id=YFINANCE, name="yfinance", role="whistleblower"),
]
FIELD_GROUPS = [
    FieldGroupRow(field_group_id=OHLC, name="ohlc"),
]


def _settings(providers=None, preferred_provider="ibkr", k=3.0) -> Settings:
    return Settings(
        debug=False,
        providers=providers if providers is not None else ["yfinance", "ibkr"],
        reconcile=ReconcileSettings(preferred_provider=preferred_provider, k=k),
    )


def _staging_row(provider_id: int, ticker_id=1, date_id=10, time_id=20, timestamp=None, **overrides) -> StagingRow:
    defaults = dict(open=100.0, high=101.0, low=99.0, close=100.5, volume=1000, incomplete=False)
    defaults.update(overrides)
    return StagingRow(
        ticker_id=ticker_id,
        date_id=date_id,
        time_id=time_id,
        timestamp=timestamp if timestamp is not None else datetime(2026, 7, 24, 13, 30),
        provider_id=provider_id,
        **defaults,
    )


def test_agreement_promotes_bar_and_purges_staging():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.01, high=101.01, low=99.01, close=100.51, volume=1002),
    ]
    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1  # ohlc only -- volume rides along with whoever wins ohlc, not its own group
    assert stuck == 0
    assert database.staging_rows == []  # purged once fully resolved
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, _open_, _high, _low, _close, volume, _incomplete = fact_row
    assert volume == 1000  # IBKR's own volume, since IBKR won ohlc
    for _, _, _, _, _, resolution_path in database.fact_reconciliation:
        assert resolution_path == "agreement"


def test_completeness_resolves_yahoo_premarket_gap():
    # The actual motivating case: Yahoo (whistleblower) reports incomplete (premarket zero
    # placeholder), IBKR (candidate) has real data -- both groups should resolve via
    # completeness, promoting IBKR's values.
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=500, incomplete=False),
        _staging_row(YFINANCE, open=0.0, high=0.0, low=0.0, close=0.0, volume=0, incomplete=True),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 1
    assert stuck == 0
    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, volume, _incomplete = fact_row
    assert (open_, high, low, close, volume) == (100.0, 101.0, 99.0, 100.5, 500)
    for _, _, _, _, _, resolution_path in database.fact_reconciliation:
        assert resolution_path == "completeness"


def test_bar_missing_a_configured_provider_is_left_alone():
    staging_rows = [_staging_row(IBKR)]  # yfinance never reported for this bar

    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_reconciliation == []
    assert len(database.staging_rows) == 1  # untouched


def test_large_disagreement_stays_stuck_until_finalize():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

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
    assert database.staging_rows == []


def test_finalize_promotes_preferred_providers_raw_value():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5, volume=1000),
    ]
    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

    # --finalize only ever touches the pending-manual-resolution queue -- a plain pass must run
    # first to actually mark this bar pending before --finalize has anything to do.
    run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=False)
    run_reconciliation(database, _settings(preferred_provider="ibkr"), finalize=True)

    fact_row = database.fact_market_data[(1, 10, 20)]
    _timestamp, open_, high, low, close, _volume, _incomplete = fact_row
    assert (open_, high, low, close) == (100.0, 101.0, 99.0, 100.5)  # IBKR's raw value, not Yahoo's


def test_agreement_updates_disagreement_stats():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
        _staging_row(YFINANCE, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
    ]
    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

    run_reconciliation(database, _settings(), finalize=False)

    updated_ohlc_stats = database.disagreement_stats[(IBKR, OHLC)]
    assert updated_ohlc_stats.sample_count == 104  # 100 seeded + 4 fields (identical values -> zero diffs)


def test_seeding_lag_converges_within_a_single_run():
    # bar_early is listed FIRST (so it's visited first in every pass) and disagrees just outside
    # the seeded tolerance (3 * 0.0008 * 100 = 0.24) -- under the old single-pass design this
    # would stay stuck for the whole run. bar_widen_1..5, listed AFTER it, each agree well inside
    # the seed tolerance and each update provider_pair_disagreement on resolution, pulling the
    # measured stddev up enough (to ~0.00104, tolerance ~0.313) that bar_early's 0.28 diff falls
    # back in range -- but only on a second pass over the still-unresolved bars, since bar_early
    # was already evaluated (and failed) earlier in the very same first pass.
    staging_rows = [
        _staging_row(IBKR, time_id=20, timestamp=datetime(2026, 7, 24, 13, 30), open=100.0, high=100.0, low=100.0, close=100.0),
        _staging_row(YFINANCE, time_id=20, timestamp=datetime(2026, 7, 24, 13, 30), open=99.72, high=99.72, low=99.72, close=99.72),
    ]
    for i in range(5):
        time_id = 30 + i
        timestamp = datetime(2026, 7, 24, 13, 40 + i)
        staging_rows.append(_staging_row(IBKR, time_id=time_id, timestamp=timestamp, open=100.0, high=100.0, low=100.0, close=100.0))
        staging_rows.append(_staging_row(YFINANCE, time_id=time_id, timestamp=timestamp, open=99.8, high=99.8, low=99.8, close=99.8))

    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 6  # bar_early + the 5 widening bars, all within one run_reconciliation() call
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
    disagreement_stats = [
        DisagreementStatsRow(provider_id=IBKR, field_group_id=OHLC, sample_count=100, running_mean=0.0, running_m2=0.000064),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows, disagreement_stats)

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
    assert len(database.staging_rows) == 2  # bar_a's rows still linger -- see comment above

    # Next plain run: bar_a is already resolved (no Tier 1-3 re-attempt), and its neighbor (bar_b)
    # is gone, so it finally purges.
    resolved, stuck = run_reconciliation(database, _settings(), finalize=False)

    assert resolved == 0  # bar_a was already resolved, nothing new to resolve
    assert stuck == 0
    assert database.staging_rows == []


def test_plain_pass_skips_an_already_pending_bar():
    staging_rows = [
        _staging_row(IBKR, open=100.0, high=101.0, low=99.0, close=100.5),
        _staging_row(YFINANCE, open=150.0, high=151.0, low=149.0, close=150.5),
    ]
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows)
    database.mark_pending_manual_resolution(1, 10, 20, OHLC)  # simulates an earlier run's flag

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
    database = FakeReconcileDatabase(PROVIDERS, FIELD_GROUPS, staging_rows)

    # No plain pass ran first, so nothing is pending -- --finalize alone has nothing to do.
    resolved, stuck = run_reconciliation(database, _settings(), finalize=True)

    assert resolved == 0
    assert stuck == 0
    assert database.fact_market_data == {}
    assert len(database.staging_rows) == 2  # untouched


def test_parse_args_defaults_finalize_to_false():
    arguments = parse_args([])

    assert arguments.finalize is False


def test_parse_args_recognizes_finalize_flag():
    arguments = parse_args(["--finalize"])

    assert arguments.finalize is True


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
