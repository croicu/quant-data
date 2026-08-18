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
    }
    payload.update(overrides)
    settings_path.write_text(json.dumps({"settings": payload}), encoding="utf-8")
    return settings_path


def test_main_parses_archived_payload_and_writes_staging_bars():
    database = MockPostgresDatabase()
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

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
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

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
            ("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD,
            ("AAPL", "yfinance", date(2026, 1, 3)): _YFINANCE_PAYLOAD,
            ("AAPL", "yfinance", date(2026, 1, 4)): _YFINANCE_PAYLOAD,
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
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

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
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

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
    archive_reader = MockProviderSourceArchiveReader({("AAPL", "yfinance", date(2026, 1, 2)): _YFINANCE_PAYLOAD})

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


def test_default_archive_reader_factory_raises_when_not_configured():
    from quant_data._internal.shared.settings import Settings

    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._default_archive_reader_factory(settings.postgres)
