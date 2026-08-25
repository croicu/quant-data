from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingest import cli
from quant_data._internal.contracts import PayloadKind, ProviderFetchResult
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.massive import MassiveIntraDay
from quant_data._internal.shared.settings import MassiveSettings, Settings
from tests.mocks.provider_source_archive import MockProviderSourceArchiveWriter
from tests.mocks.yfinance import MockIntraDayProvider

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"


class _FailingProvider:
    """Fake IntraDayProvider that fails at a chosen lifecycle stage, for testing that one
    provider's failure doesn't sink providers that succeed."""

    FETCH_VERSION = "1"
    DEFAULT_METHODS = ["TEST"]

    def __init__(self, fail_on: str = "fetch_bars") -> None:
        self.fail_on = fail_on
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True
        if self.fail_on == "connect":
            raise AppError("connect failed")

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        raise AppError("fetch failed")

    def close(self) -> None:
        self.closed = True


def _use_archive_writer(writer):
    def factory(postgres_settings):
        return writer

    return factory


class _FailingArchiveWriter:
    """Fake ProviderSourceArchiveWriter whose record_fetch always raises -- for testing that a
    broken archive write is treated as this provider's failure for that (ticker, date), same as a
    fetch failure would be."""

    def __init__(self) -> None:
        self.closed = False

    def record_fetch(self, ticker: str, provider: str, method: str, trading_date: date, fetch_version: str, payload_kind, payload: dict) -> None:
        raise AppError("archive write failed")

    def close(self) -> None:
        self.closed = True


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


def test_main_logs_verbose_start_message_per_chunk(tmp_path, capsys):
    # Regression guard: with only a start-of-run and an end-of-chunk log line, a long-running
    # archive write looked indistinguishable from a hang -- this per-chunk "starting" line at
    # VERBOSE gives a heartbeat.
    # debug=True is required here, not just logLevel="verbose" -- with logCategories left
    # unset, debug=False resolves the category allow-list to ["general"] only (see
    # CLAUDE.md's Logging section), which would silently filter out this "ingest"-category
    # line regardless of level.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], logLevel="verbose", debug=True)
    archive_writer = MockProviderSourceArchiveWriter()

    cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
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
    archive_writer = MockProviderSourceArchiveWriter()
    fake_limiter = _CountingRateLimiter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
        rate_limiters={"yfinance": fake_limiter},
    )

    assert fake_limiter.acquire_calls == 1


def test_main_fetches_and_archives_and_returns_zero():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1
    assert archive_writer.closed is True


def test_main_returns_one_on_unknown_ticker():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "NOTINFIXTURE", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    assert archive_writer.recorded_fetches == []


def test_main_uses_settings_tickers_when_ticker_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1


def test_main_batch_mode_continues_after_one_ticker_fails(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl", "notinfixture"])
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    # AAPL still gets archived even though the second ticker failed -- one bad ticker
    # shouldn't sink the whole batch.
    assert len(archive_writer.recorded_fetches) == 1


def test_main_returns_one_when_no_ticker_and_no_settings_tickers():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    assert archive_writer.recorded_fetches == []


def test_main_iterates_over_date_range_and_tolerates_gaps():
    archive_writer = MockProviderSourceArchiveWriter()

    # 2026-01-02 (Fri) and 2026-01-05 (Mon) have fixture data and archive for real; 01-03/01-04
    # (weekend) get marked covered-without-data instead (croicu/quant-data#60) -- both outcomes
    # count as handled, not failed (croicu/quant-data#71), so the whole range succeeds.
    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-05"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 2  # 01-02 and 01-05 each archive once


def test_main_uses_settings_date_range_when_dates_omitted(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1


def test_main_cli_dates_override_settings_date_range(tmp_path):
    settings_path = _custom_settings(tmp_path, startDate="2026-01-02", endDate="2026-01-02")
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-05", "--end-date", "2026-01-05"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1


def test_main_returns_one_with_no_args_and_nothing_configured():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    assert archive_writer.recorded_fetches == []


def test_main_exits_two_on_malformed_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--start-date", "not-a-date", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_end_date_before_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--start-date", "2026-01-05", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_defaults_end_date_to_start_date_for_single_day():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1


def test_main_exits_two_when_only_end_date_given():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--end-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_exits_two_when_catch_up_combined_with_start_date():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL", "--catch-up", "--start-date", "2026-01-02"])

    assert exc_info.value.code == 2


def test_main_catch_up_uses_settings_lookback_window(tmp_path):
    # today=2026-01-03, catchUpLookbackDays=1 -> re-fetches exactly 2026-01-02, which has fixture
    # data for AAPL.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], catchUpLookbackDays=1)
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--catch-up"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
        today=lambda: date(2026, 1, 3),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1


def test_main_catch_up_excludes_today_and_tolerates_gaps(tmp_path):
    # today=2026-01-06, default 7-day lookback -> 2025-12-30 through 2026-01-05 inclusive.
    # Only 01-02 and 01-05 have fixture data; the rest fail per-day without aborting the run, same
    # as a plain --start-date/--end-date range.
    settings_path = _custom_settings(tmp_path, tickers=["aapl"])
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--catch-up"],
        settings_path=settings_path,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
        today=lambda: date(2026, 1, 6),
    )

    assert exit_code == 1
    assert len(archive_writer.recorded_fetches) == 2  # 01-02 and 01-05


def test_parse_args_providers_splits_comma_separated_names():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--providers", "yfinance,massive"])

    assert arguments.providers == ["yfinance", "massive"]


def test_parse_args_providers_lowercases_and_trims():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--providers", " Yfinance , MASSIVE "])

    assert arguments.providers == ["yfinance", "massive"]


def test_parse_args_providers_defaults_to_none_when_omitted():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02"])

    assert arguments.providers is None


def test_parse_args_providers_rejects_empty_name():
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--providers", "yfinance,,massive"])

    assert exc_info.value.code == 2


def test_parse_args_reads_response_file(tmp_path):
    response_file = tmp_path / "no-ibkr.args"
    response_file.write_text("--providers\nyfinance,massive\n", encoding="utf-8")

    arguments = cli.parse_args([f"@{response_file}", "--ticker", "SPY", "--start-date", "2026-01-02"])

    assert arguments.providers == ["yfinance", "massive"]
    assert arguments.ticker == "SPY"


def test_parse_args_ibkr_methods_splits_comma_separated_names():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--ibkr-methods", "TRADES,BID_ASK"])

    assert arguments.ibkr_methods == ["TRADES", "BID_ASK"]


def test_parse_args_ibkr_methods_strips_but_does_not_lowercase():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--ibkr-methods", " TRADES , BID_ASK "])

    assert arguments.ibkr_methods == ["TRADES", "BID_ASK"]


def test_parse_args_ibkr_methods_defaults_to_none_when_omitted():
    arguments = cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02"])

    assert arguments.ibkr_methods is None


def test_parse_args_ibkr_methods_rejects_empty_name():
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--ticker", "AAPL", "--start-date", "2026-01-02", "--ibkr-methods", "TRADES,,MIDPOINT"])

    assert exc_info.value.code == 2


def test_main_ibkr_methods_flag_overrides_settings_ibkr_methods():
    # No providers= kwarg injected -- exercises the real settings.ibkr.methods -> _methods_for
    # path. IBKRIntraDay itself is passed in as a mock (avoids a real Gateway connection) so we can
    # inspect what _methods_for actually resolved to via the archived fetch's recorded method.
    from tests.mocks.yfinance import MockIntraDayProvider

    ibkr_provider = MockIntraDayProvider()
    ibkr_provider.DEFAULT_METHODS = ["TRADES", "BID_ASK", "MIDPOINT"]
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "AAPL", "--start-date", "2026-01-02", "--end-date", "2026-01-02", "--ibkr-methods", "TRADES"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": ibkr_provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    # Only TRADES fetched/archived -- BID_ASK/MIDPOINT (the provider's own full default set) were
    # not, proving --ibkr-methods actually restricted settings.ibkr.methods for this run.
    recorded_methods = [call[2] for call in archive_writer.recorded_fetches]
    assert recorded_methods == ["TRADES"]


def test_main_providers_flag_overrides_settings_providers_without_explicit_injection(tmp_path):
    # No providers= kwarg passed to main() -- this exercises the real settings.providers ->
    # _default_providers path, not the test-only override every other test in this file uses.
    # Default settings.json has no "providers" key (defaults to ["yfinance"]); --providers massive
    # should still route to _build_provider("massive", ...), which fails fast since
    # settings.massive isn't configured in this fixture -- proving the CLI flag actually replaced
    # settings.providers rather than being ignored.
    exit_code = cli.main(
        ["--ticker", "AAPL", "--start-date", "2026-01-02", "--providers", "massive"],
        settings_path=SETTINGS_PATH,
        archive_writer_factory=_use_archive_writer(MockProviderSourceArchiveWriter()),
    )

    assert exit_code == 1


def test_main_archives_fetch_tagged_with_provider_name():
    archive_writer = MockProviderSourceArchiveWriter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert len(archive_writer.recorded_fetches) == 1
    ticker, provider, method, trading_date, fetch_version, payload_kind, payload = archive_writer.recorded_fetches[0]
    assert ticker == "AAPL"
    assert provider == "yfinance"
    assert method == "history"
    assert trading_date == date(2026, 1, 2)
    assert fetch_version == "1"
    assert payload_kind == PayloadKind.PARSED_BARS
    assert len(payload["bars"]) == 2  # AAPL 2026-01-02 has 2 fixture bars


def test_main_does_not_archive_when_fetch_fails():
    archive_writer = MockProviderSourceArchiveWriter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": _FailingProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert archive_writer.recorded_fetches == []


def test_main_fails_the_date_when_archive_write_fails():
    # Archiving is the whole job now (croicu/quant-data#56) -- a broken quant_ingest write is no
    # longer a secondary, tolerable failure the way it was when staging was the primary output.
    archive_writer = _FailingArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    assert archive_writer.closed is True


def test_main_fails_immediately_when_archive_writer_factory_fails():
    def failing_factory(postgres_settings):
        raise AppError("cannot connect to quant_ingest")

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider()},
        archive_writer_factory=failing_factory,
    )

    assert exit_code == 1


def test_default_archive_writer_factory_raises_when_not_configured():
    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._default_archive_writer_factory(settings.postgres)


def test_main_connects_and_closes_every_provider():
    archive_writer = MockProviderSourceArchiveWriter()
    yfinance_provider = MockIntraDayProvider()
    ibkr_provider = MockIntraDayProvider()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": yfinance_provider, "ibkr": ibkr_provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert yfinance_provider.connected is True
    assert yfinance_provider.closed is True
    assert ibkr_provider.connected is True
    assert ibkr_provider.closed is True


def test_main_succeeds_when_only_one_of_two_providers_can_fetch():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": MockIntraDayProvider(), "ibkr": _FailingProvider(fail_on="fetch_bars")},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    # Only the working provider's fetch is archived -- the failing one is logged and skipped, not
    # allowed to sink the whole (ticker, date) pair.
    assert len(archive_writer.recorded_fetches) == 1
    assert archive_writer.recorded_fetches[0][1] == "yfinance"


def test_main_drops_a_provider_that_fails_to_connect_and_continues_with_the_rest():
    archive_writer = MockProviderSourceArchiveWriter()
    working_provider = MockIntraDayProvider()
    broken_provider = _FailingProvider(fail_on="connect")

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"yfinance": working_provider, "ibkr": broken_provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1
    # A provider that fails to connect is never asked to fetch or close.
    assert broken_provider.closed is False


def test_main_fails_fast_when_no_providers_configured(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], providers=[])

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        archive_writer_factory=_use_archive_writer(MockProviderSourceArchiveWriter()),
    )

    assert exit_code == 1


def test_main_fails_when_every_provider_fails_to_connect():
    archive_writer = MockProviderSourceArchiveWriter()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": _FailingProvider(fail_on="connect")},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 1
    assert archive_writer.recorded_fetches == []


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


# --- Multi-method fetching (croicu/quant-data#60) ---


class _MultiMethodProvider:
    """Fake IntraDayProvider with more than one DEFAULT_METHODS entry (mirroring IBKRIntraDay's
    real TRADES + BID_ASK), for testing ingest/cli.py's per-method loop without a real IBKR
    connection."""

    FETCH_VERSION = "1"
    DEFAULT_METHODS = ["TRADES", "BID_ASK"]

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.requested_methods: list[str] = []

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        effective_method = method if method is not None else self.DEFAULT_METHODS[0]
        self.requested_methods.append(effective_method)
        return ProviderFetchResult(payload={"bars": []}, payload_kind=PayloadKind.PARSED_BARS, method=effective_method)


def test_main_fetches_all_default_methods_when_none_specified():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _MultiMethodProvider()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert provider.requested_methods == ["TRADES", "BID_ASK"]
    assert len(archive_writer.recorded_fetches) == 2
    archived_methods = []
    for fetch in archive_writer.recorded_fetches:
        archived_methods.append(fetch[2])
    assert archived_methods == ["TRADES", "BID_ASK"]


def test_main_restricts_to_settings_ibkr_methods_override(tmp_path):
    settings_path = _custom_settings(tmp_path, tickers=["aapl"], ibkr={"methods": ["BID_ASK"]})
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _MultiMethodProvider()

    exit_code = cli.main(
        ["--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=settings_path,
        providers={"ibkr": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert provider.requested_methods == ["BID_ASK"]
    assert len(archive_writer.recorded_fetches) == 1


def test_main_calls_rate_limiter_once_per_method():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _MultiMethodProvider()
    fake_limiter = _CountingRateLimiter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
        rate_limiters={"ibkr": fake_limiter},
    )

    assert fake_limiter.acquire_calls == 2


def test_main_archives_remaining_methods_when_one_method_fails():
    # Per-method fault tolerance mirrors the existing per-provider tolerance: one method failing
    # (e.g. a pacing violation on the second call) doesn't lose the methods that succeeded.
    class _PartiallyFailingProvider(_MultiMethodProvider):
        def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
            if method == "BID_ASK":
                raise AppError("pacing violation")
            return super().fetch_bars(ticker, target_date, method)

    archive_writer = MockProviderSourceArchiveWriter()
    provider = _PartiallyFailingProvider()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],
        settings_path=SETTINGS_PATH,
        providers={"ibkr": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert exit_code == 0
    assert len(archive_writer.recorded_fetches) == 1
    assert archive_writer.recorded_fetches[0][2] == "TRADES"


def test_methods_for_returns_provider_default_methods_when_no_settings_override():
    settings = Settings.load(path=SETTINGS_PATH)
    provider = _MultiMethodProvider()

    assert cli._methods_for("ibkr", settings, provider) == ["TRADES", "BID_ASK"]


def test_methods_for_returns_settings_override_for_ibkr(tmp_path):
    settings_path = _custom_settings(tmp_path, ibkr={"methods": ["TRADES"]})
    settings = Settings.load(path=settings_path)
    provider = _MultiMethodProvider()

    assert cli._methods_for("ibkr", settings, provider) == ["TRADES"]


def test_methods_for_ignores_ibkr_settings_override_for_other_providers(tmp_path):
    settings_path = _custom_settings(tmp_path, ibkr={"methods": ["TRADES"]})
    settings = Settings.load(path=settings_path)

    assert cli._methods_for("yfinance", settings, MockIntraDayProvider()) == ["history"]


# --- Weekend consolidation (croicu/quant-data#60) ---


class _TrackingProvider:
    """Fake IntraDayProvider that records every fetch_bars() call it actually receives, for
    proving a weekend-skipped (provider, date) pair never reaches fetch_bars at all."""

    FETCH_VERSION = "1"
    DEFAULT_METHODS = ["PRIMARY"]

    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, date]] = []

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        self.fetch_calls.append((ticker, target_date))
        return ProviderFetchResult(payload={"bars": []}, payload_kind=PayloadKind.PARSED_BARS, method=self.DEFAULT_METHODS[0])


def test_main_marks_weekend_covered_without_data_instead_of_fetching_for_non_ibkr():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _TrackingProvider()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-03", "--end-date", "2026-01-04"],  # Sat/Sun
        settings_path=SETTINGS_PATH,
        providers={"yfinance": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert provider.fetch_calls == []
    assert archive_writer.recorded_fetches == []
    assert len(archive_writer.covered_without_data) == 2
    for _ticker, provider_name, method, _trading_date, fetch_version in archive_writer.covered_without_data:
        assert provider_name == "yfinance"
        assert method == "PRIMARY"
        assert fetch_version == "1"
    # Marked-covered-without-data counts as handled, not failed (croicu/quant-data#71) -- the
    # opposite of this feature's original accounting. A weekend genuinely has no data to write, but
    # it's still a correct, terminal outcome, not an error to retry. Counting it as "failed" was
    # harmless for a human-run --catch-up, but became a real bug once quant-schedule (#68) started
    # creating real run_once jobs for weekend dates: those jobs would never report success, so
    # they'd retry forever instead of disabling once handled.
    assert exit_code == 0


def test_main_does_not_skip_weekend_fetch_for_ibkr():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _TrackingProvider()

    exit_code = cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-03", "--end-date", "2026-01-03"],  # Sat
        settings_path=SETTINGS_PATH,
        providers={"ibkr": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert provider.fetch_calls == [("aapl", date(2026, 1, 3))]
    assert archive_writer.covered_without_data == []
    assert exit_code == 0


def test_main_does_not_acquire_rate_limiter_for_weekend_skip():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _TrackingProvider()
    fake_limiter = _CountingRateLimiter()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-03", "--end-date", "2026-01-03"],  # Sat
        settings_path=SETTINGS_PATH,
        providers={"yfinance": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
        rate_limiters={"yfinance": fake_limiter},
    )

    assert fake_limiter.acquire_calls == 0


def test_main_does_not_skip_weekday_dates():
    archive_writer = MockProviderSourceArchiveWriter()
    provider = _TrackingProvider()

    cli.main(
        ["--ticker", "aapl", "--start-date", "2026-01-02", "--end-date", "2026-01-02"],  # Fri
        settings_path=SETTINGS_PATH,
        providers={"yfinance": provider},
        archive_writer_factory=_use_archive_writer(archive_writer),
    )

    assert provider.fetch_calls == [("aapl", date(2026, 1, 2))]
    assert archive_writer.covered_without_data == []
