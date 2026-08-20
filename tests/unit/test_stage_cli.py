from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from quant_data._internal.contracts import PayloadKind
from quant_data._internal.shared.errors import AppError
from stage import cli
from tests.mocks.postgres import MockPostgresDatabase
from tests.mocks.provider_source_archive import MockProviderSourceArchiveReader

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"

_YFINANCE_PAYLOAD = (
    PayloadKind.PARSED_BARS,
    {
        "bars": [
            {"timestamp": "2026-01-02T14:30:00+00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
            {"timestamp": "2026-01-02T14:31:00+00:00", "open": 100.5, "high": 100.9, "low": 100.0, "close": 100.2, "volume": 500},
        ]
    },
)


def _use_database(database: MockPostgresDatabase):
    def factory(postgres_settings):
        return database

    return factory


def _use_archive_reader(reader: MockProviderSourceArchiveReader):
    def factory(postgres_settings):
        return reader

    return factory


def _custom_settings(tmp_path: Path, **overrides) -> Path:
    settings_path = tmp_path / "settings.json"
    payload = {
        "debug": False,
        "logLevel": "error",
        "postgres": {"host": "localhost", "port": 5433, "user": "test", "password": "test", "dbname": "test"},
        "providers": ["yfinance"],
    }
    payload.update(overrides)
    settings_path.write_text(json.dumps({"settings": payload}), encoding="utf-8")
    return settings_path


def test_main_parses_archived_payload_and_writes_staging_bars():
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2
    for provider_name, _ in database.written_staging_bars:
        assert provider_name == "yfinance"
    assert database.closed is True
    assert archive_reader.closed is True


def test_main_records_ingestion_coverage_on_successful_stage():
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert database.recorded_coverage == [("yfinance", "AAPL", date(2026, 1, 2))]


def test_main_skips_weekend_dates_without_touching_the_archive():
    # 2026-01-03/2026-01-04 are a Sat/Sun -- even though something's archived under those exact
    # dates (e.g. ibkr's own quirk of returning the prior trading day's session for a weekend
    # request, see croicu/quant-data#56), stage shouldn't re-stage it a second time under the
    # weekend date.
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader(
        {
            ("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD,
            ("AAPL", "yfinance", "history", date(2026, 1, 3)): _YFINANCE_PAYLOAD,
            ("AAPL", "yfinance", "history", date(2026, 1, 4)): _YFINANCE_PAYLOAD,
        }
    )

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-04"],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    # Only Friday 01-02's 2 bars land -- the weekend dates are skipped entirely, not staged and
    # not counted as failures either.
    assert len(database.written_staging_bars) == 2


def test_main_fails_fast_when_no_providers_configured(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], providers=[])

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        database_factory=_use_database(MockPostgresDatabase()),
        archive_reader_factory=_use_archive_reader(MockProviderSourceArchiveReader({})),
    )

    assert exit_code == 1


def test_main_returns_one_when_nothing_archived_for_the_date():
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({})

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 1
    assert database.written_staging_bars == []


def test_main_uses_settings_tickers_when_ticker_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_iterates_multiple_configured_providers_independently(tmp_path):
    settings_path = _custom_settings(tmp_path, providers=["yfinance", "ibkr"])
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    # yfinance has archived data and stages successfully; ibkr has nothing archived for this
    # (ticker, date) -- not staged, but doesn't sink the run since yfinance still succeeded.
    assert exit_code == 0
    assert len(database.written_staging_bars) == 2
    for provider_name, _ in database.written_staging_bars:
        assert provider_name == "yfinance"


def test_main_catch_up_uses_settings_lookback_window(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], catchUpLookbackDays=1)
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    exit_code = cli.main(
        ["--catch-up"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
        today=lambda: date(2026, 1, 3),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2


def test_main_exits_two_on_malformed_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--start-date", "not-a-date", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_catch_up_combined_with_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--catch-up", "--start-date", "2026-01-02"])

    assert exc_info.value.code == 2


_IBKR_TRADES_PAYLOAD = (
    PayloadKind.PARSED_BARS,
    {
        "bars": [
            {"timestamp": "2026-01-02T14:30:00+00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
        ]
    },
)

_IBKR_BID_ASK_PAYLOAD = (
    PayloadKind.PARSED_BARS,
    {"bars": [{"timestamp": "2026-01-02T14:30:00+00:00", "avg_bid": 100.0, "avg_ask": 100.1, "high": 100.2, "low": 99.9}]},
)

_IBKR_MIDPOINT_PAYLOAD = (
    PayloadKind.PARSED_BARS,
    {"bars": [{"timestamp": "2026-01-02T14:30:00+00:00", "open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1}]},
)


def test_main_merges_ibkr_supplementary_methods_into_primary_bars(tmp_path):
    # croicu/quant-data#61: BID_ASK/MIDPOINT are archived separately from TRADES but land on the
    # same staging row, keyed by timestamp, rather than becoming their own rows.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], providers=["ibkr"])
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader(
        {
            ("AAPL", "ibkr", "TRADES", date(2026, 1, 2)): _IBKR_TRADES_PAYLOAD,
            ("AAPL", "ibkr", "BID_ASK", date(2026, 1, 2)): _IBKR_BID_ASK_PAYLOAD,
            ("AAPL", "ibkr", "MIDPOINT", date(2026, 1, 2)): _IBKR_MIDPOINT_PAYLOAD,
        }
    )

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 1
    _provider_name, bar = database.written_staging_bars[0]
    assert (bar.open, bar.volume) == (100.0, 1000)  # primary TRADES fields untouched
    assert (bar.avg_bid, bar.avg_ask) == (100.0, 100.1)
    assert (bar.midpoint_open, bar.midpoint_high, bar.midpoint_low, bar.midpoint_close) == (100.0, 100.2, 99.9, 100.1)


def test_main_stages_ibkr_bar_when_only_trades_is_archived(tmp_path):
    # A day archived before croicu/quant-data#62 shipped MIDPOINT, or a settings.ibkr.methods
    # restriction -- supplementary methods simply aren't archived, not an error.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], providers=["ibkr"])
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "ibkr", "TRADES", date(2026, 1, 2)): _IBKR_TRADES_PAYLOAD})

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 1
    _provider_name, bar = database.written_staging_bars[0]
    assert bar.avg_bid is None
    assert bar.midpoint_open is None


def test_parse_args_providers_splits_comma_separated_names():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--providers", "yfinance,massive"])

    assert arguments.providers == ["yfinance", "massive"]


def test_parse_args_reads_response_file(tmp_path):
    response_file = tmp_path / "no-ibkr.args"
    response_file.write_text("--providers\nyfinance,massive\n", encoding="utf-8")

    arguments = cli.parse_args([f"@{response_file}", "--ticker", "SPY", "--start-date", "2026-01-02"])

    assert arguments.providers == ["yfinance", "massive"]
    assert arguments.ticker == "SPY"


def test_main_providers_flag_overrides_settings_providers(tmp_path):
    # settings.providers defaults to ["ibkr"] here (nothing archived for ibkr in this test's
    # archive_reader), but --providers yfinance should override it entirely -- proving the flag
    # actually reaches _stage_one's provider loop, not just parsed and discarded.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], providers=["ibkr"])
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", "history", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02", "--providers", "yfinance"],
        settings_path=settings_path,
        database_factory=_use_database(database),
        archive_reader_factory=_use_archive_reader(archive_reader),
    )

    assert exit_code == 0
    assert len(database.written_staging_bars) == 2
    for provider_name, _ in database.written_staging_bars:
        assert provider_name == "yfinance"


def test_default_archive_reader_factory_raises_when_not_configured():
    from quant_data._internal.shared.settings import Settings

    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._default_archive_reader_factory(settings.postgres)
