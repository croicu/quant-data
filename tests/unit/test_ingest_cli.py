from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingest import cli
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.massive import MassiveIntraDay
from quant_data._internal.shared.settings import MassiveSettings, Settings
from tests.mocks.postgres import MockPostgresDatabase
from tests.mocks.yfinance import MockIntraDayProvider

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"


class _RecordingProvider:
    """Fake IntraDayProvider that records every (ticker, date) it was asked to fetch and always
    succeeds with no bars -- lets --backfill tests assert exactly which dates were fetched without
    depending on the yfinance fixture's specific covered dates."""

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.fetch_calls: list[tuple[str, date]] = []

    def connect(self) -> None:
        self.connected = True

    def fetch_bars(self, ticker: str, target_date: date):
        self.fetch_calls.append((ticker.upper(), target_date))
        return []

    def close(self) -> None:
        self.closed = True


class _FailingProvider:
    """Fake IntraDayProvider that fails at a chosen lifecycle stage, for testing that one
    provider's failure doesn't sink providers that succeed."""

    def __init__(self, fail_on: str = "fetch_bars") -> None:
        self.fail_on = fail_on
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True
        if self.fail_on == "connect":
            raise AppError("connect failed")

    def fetch_bars(self, ticker: str, target_date: date):
        raise AppError("fetch failed")

    def close(self) -> None:
        self.closed = True


def _use_database(database: MockPostgresDatabase):
    def factory(postgres_settings):
        return database

    return factory


def _custom_settings(tmp_path: Path, **overrides) -> Path:
    settings_path = tmp_path / "settings.json"
    payload = {
        "debug": False,
        "logLevel": "error",
        "postgres": {"host": "localhost", "port": 5433, "user": "test", "password": "test", "dbname": "test"},
    }
    payload.update(overrides)
    settings_path.write_text(json.dumps({"settings": payload}), encoding="utf-8")
    return settings_path


def test_main_logs_verbose_start_message_per_chunk(tmp_path, capsys):
    # Regression guard: with only a start-of-run and an end-of-chunk log line, a long-running
    # write (write_bars does several round trips per bar) looked indistinguishable from a hang --
    # this per-chunk "starting" line at VERBOSE gives a heartbeat.
    # debug=True is required here, not just logLevel="verbose" -- with logCategories left
    # unset, debug=False resolves the category allow-list to ["general"] only (see
    # CLAUDE.md's Logging section), which would silently filter out this "ingest"-category
    # line regardless of level.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], logLevel="verbose", debug=True)
    database = MockPostgresDatabase()

    cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    captured = capsys.readouterr()
    assert "[VERBOSE][ingest] quant-ingest: starting AAPL on 2026-01-02." in captured.out


class _CountingRateLimiter:
    """Fake RateLimiter that just counts acquire() calls, for asserting it's invoked once per
    actual fetch_bars call -- not per (ticker, date) pair, not per connect failure."""

    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self) -> None:
        self.acquire_calls += 1


def test_main_calls_rate_limiter_once_per_fetch_bars_call():
    database = MockPostgresDatabase()
    fake_limiter = _CountingRateLimiter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
        rate_limiters={"yfinance": fake_limiter},
    )

    assert fake_limiter.acquire_calls == 1


def test_main_fetches_and_writes_bars_and_returns_zero():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2
    assert database.closed is True


def test_main_returns_one_on_unknown_ticker():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "NOTINFIXTURE", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 0


def test_main_uses_settings_tickers_when_ticker_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_batch_mode_continues_after_one_ticker_fails(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl", "notinfixture"])
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    # AAPL still gets written even though the second ticker failed -- one bad ticker
    # shouldn't sink the whole batch.
    assert len(database.written_staging_bars) == 2


def test_main_returns_one_when_no_ticker_and_no_settings_tickers():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 0


def test_main_iterates_over_date_range_and_tolerates_gaps():
    database = MockPostgresDatabase()

    # 2026-01-02 (Fri) and 2026-01-05 (Mon) have fixture data; 01-03/01-04 (weekend) don't --
    # those two days should fail per-date without aborting the rest of the range.
    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-05"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 3  # 2 bars on 01-02 + 1 bar on 01-05


def test_main_uses_settings_date_range_when_dates_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_cli_dates_override_settings_date_range(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-05", "--end-date", "2026-01-05"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 1  # 2026-01-05's single bar, not 2026-01-02's two


def test_main_returns_one_with_no_args_and_nothing_configured():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 0


def test_main_exits_two_on_malformed_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--start-date", "not-a-date", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_end_date_before_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--start-date", "2026-01-05", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_defaults_end_date_to_start_date_for_single_day():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_exits_two_when_only_end_date_given():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_catch_up_combined_with_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--catch-up", "--start-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_catch_up_uses_settings_lookback_window(tmp_path):
    # today=2026-01-03, catchUpLookbackDays=1 -> re-fetches exactly 2026-01-02, which has 2
    # fixture bars for AAPL.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], catchUpLookbackDays=1)
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--catch-up"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
        today=lambda: date(2026, 1, 3),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_catch_up_excludes_today_and_tolerates_gaps(tmp_path):
    # today=2026-01-06, default 7-day lookback -> 2025-12-30 through 2026-01-05 inclusive.
    # Only 01-02 (2 bars) and 01-05 (1 bar) have fixture data; the rest fail per-day without
    # aborting the run, same as a plain --start-date/--end-date range.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--catch-up"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
        today=lambda: date(2026, 1, 6),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 3  # 2 bars on 01-02 + 1 bar on 01-05


def test_main_exits_two_when_backfill_combined_with_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--backfill", "--start-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_backfill_combined_with_catch_up():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--backfill", "--catch-up"])

    assert exc_info.value.code == 2


def test_main_backfill_fetches_one_chunk_walking_backward(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], backfillChunkDays=3)
    database = MockPostgresDatabase(
        inception_date=date(2026, 1, 1),
        earliest_covered_by_ticker={"AAPL": date(2026, 1, 10)},
    )
    provider = _RecordingProvider()

    exit_code = cli.main(
        ["--backfill"],
        settings_path=settings_path,
        providers={"yfinance": provider},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert provider.fetch_calls == [
        ("AAPL", date(2026, 1, 7)),
        ("AAPL", date(2026, 1, 8)),
        ("AAPL", date(2026, 1, 9)),
    ]


def test_main_backfill_is_a_no_op_once_a_ticker_reaches_inception(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], backfillChunkDays=3)
    database = MockPostgresDatabase(
        inception_date=date(2026, 1, 1),
        earliest_covered_by_ticker={"AAPL": date(2026, 1, 1)},
    )
    provider = _RecordingProvider()

    exit_code = cli.main(
        ["--backfill"],
        settings_path=settings_path,
        providers={"yfinance": provider},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert provider.fetch_calls == []


def test_main_backfill_bootstraps_from_today_for_a_never_ingested_ticker(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], backfillChunkDays=2)
    database = MockPostgresDatabase(inception_date=date(2020, 1, 1), earliest_covered_by_ticker={})
    provider = _RecordingProvider()

    exit_code = cli.main(
        ["--backfill"],
        settings_path=settings_path,
        providers={"yfinance": provider},
        database_factory=_use_database(database),
        today=lambda: date(2026, 1, 10),
    )

    assert exit_code == 0
    assert provider.fetch_calls == [
        ("AAPL", date(2026, 1, 8)),
        ("AAPL", date(2026, 1, 9)),
    ]


def test_main_backfill_advances_every_ticker_one_chunk_round_robin(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl", "msft"], backfillChunkDays=1)
    database = MockPostgresDatabase(
        inception_date=date(2020, 1, 1),
        earliest_covered_by_ticker={"AAPL": date(2026, 1, 10), "MSFT": date(2026, 2, 1)},
    )
    provider = _RecordingProvider()

    exit_code = cli.main(
        ["--backfill"],
        settings_path=settings_path,
        providers={"yfinance": provider},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    # Each ticker advances exactly one chunk this invocation -- AAPL doesn't get drained toward
    # inception_date before MSFT is even touched.
    assert provider.fetch_calls == [("AAPL", date(2026, 1, 9)), ("MSFT", date(2026, 1, 31))]


def test_main_backfill_returns_one_when_dataset_inception_is_empty(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    database = MockPostgresDatabase(inception_date=None)
    provider = _RecordingProvider()

    exit_code = cli.main(
        ["--backfill"],
        settings_path=settings_path,
        providers={"yfinance": provider},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert provider.fetch_calls == []


def test_main_writes_staging_bars_tagged_with_provider_name():
    database = MockPostgresDatabase()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert len(database.written_staging_bars) == 2
    for provider_name, _ in database.written_staging_bars:
        assert provider_name == "yfinance"


def test_main_records_ingestion_coverage_on_successful_fetch_and_write():
    database = MockPostgresDatabase()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        database_factory=_use_database(database),
    )

    assert database.recorded_coverage == [("yfinance", "AAPL", date(2026, 1, 2))]


def test_main_does_not_record_coverage_when_fetch_fails():
    database = MockPostgresDatabase()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": _FailingProvider()},
        database_factory=_use_database(database),
    )

    assert database.recorded_coverage == []


def test_main_connects_and_closes_every_provider():
    database = MockPostgresDatabase()
    yfinance_provider = MockIntraDayProvider()
    ibkr_provider = MockIntraDayProvider()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": yfinance_provider, "ibkr": ibkr_provider},
        database_factory=_use_database(database),
    )

    assert yfinance_provider.connected is True
    assert yfinance_provider.closed is True
    assert ibkr_provider.connected is True
    assert ibkr_provider.closed is True


def test_main_succeeds_when_only_one_of_two_providers_can_fetch():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider(), "ibkr": _FailingProvider(fail_on="fetch_bars")},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    # Only the working provider's bars land in staging -- the failing one is logged and skipped,
    # not allowed to sink the whole (ticker, date) pair.
    assert len(database.written_staging_bars) == 2
    for provider_name, _ in database.written_staging_bars:
        assert provider_name == "yfinance"


def test_main_drops_a_provider_that_fails_to_connect_and_continues_with_the_rest():
    database = MockPostgresDatabase()
    working_provider = MockIntraDayProvider()
    broken_provider = _FailingProvider(fail_on="connect")

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": working_provider, "ibkr": broken_provider},
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2
    # A provider that fails to connect is never asked to fetch or close.
    assert broken_provider.closed is False


def test_main_fails_when_every_provider_fails_to_connect():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": _FailingProvider(fail_on="connect")},
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_staging_bars) == 0


def test_build_provider_raises_on_unknown_provider_name():
    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._build_provider("not-a-real-provider", settings)


def test_build_provider_returns_massive_intraday_with_configured_api_key():
    settings = Settings.load(path=SETTINGS_PATH)
    settings.massive = MassiveSettings(api_key="test-key")

    provider = cli._build_provider("massive", settings)

    assert isinstance(provider, MassiveIntraDay)


def test_build_provider_raises_when_massive_in_providers_without_settings_massive():
    settings = Settings.load(path=SETTINGS_PATH)  # settings.massive is None

    with pytest.raises(AppError):
        cli._build_provider("massive", settings)


def test_rate_limit_for_massive_returns_configured_default():
    settings = Settings.load(path=SETTINGS_PATH)
    settings.massive = MassiveSettings(api_key="test-key")

    rate_limit = cli._rate_limit_for("massive", settings)

    assert rate_limit is not None
    assert rate_limit.requests_per_window == 5
    assert rate_limit.window_seconds == 60


def test_rate_limit_for_massive_returns_none_when_not_configured():
    settings = Settings.load(path=SETTINGS_PATH)

    assert cli._rate_limit_for("massive", settings) is None
