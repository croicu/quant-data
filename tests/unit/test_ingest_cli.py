from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest import cli
from tests.mocks.postgres import MockPostgresDatabase
from tests.mocks.yf import MockIntraDayProvider

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"


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


def test_main_fetches_and_writes_bars_and_returns_zero():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_bars) == 2
    assert database.closed is True


def test_main_returns_one_on_unknown_ticker():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "NOTINFIXTURE", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_bars) == 0


def test_main_uses_settings_tickers_when_ticker_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_bars) == 2


def test_main_batch_mode_continues_after_one_ticker_fails(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl", "notinfixture"])
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    # AAPL still gets written even though the second ticker failed -- one bad ticker
    # shouldn't sink the whole batch.
    assert len(database.written_bars) == 2


def test_main_returns_one_when_no_ticker_and_no_settings_tickers():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_bars) == 0


def test_main_iterates_over_date_range_and_tolerates_gaps():
    database = MockPostgresDatabase()

    # 2026-01-02 (Fri) and 2026-01-05 (Mon) have fixture data; 01-03/01-04 (weekend) don't --
    # those two days should fail per-date without aborting the rest of the range.
    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-05"],
        settings_path=SETTINGS_PATH,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_bars) == 3  # 2 bars on 01-02 + 1 bar on 01-05


def test_main_uses_settings_date_range_when_dates_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl"],
        settings_path=settings_path,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_bars) == 2


def test_main_cli_dates_override_settings_date_range(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    database = MockPostgresDatabase()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-05", "--end-date", "2026-01-05"],
        settings_path=settings_path,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_bars) == 1  # 2026-01-05's single bar, not 2026-01-02's two


def test_main_returns_one_with_no_args_and_nothing_configured():
    database = MockPostgresDatabase()

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 1
    assert len(database.written_bars) == 0


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
        provider=MockIntraDayProvider(),
        database_factory=_use_database(database),
    )

    assert exit_code == 0
    assert len(database.written_bars) == 2


def test_main_exits_two_when_only_end_date_given():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2
