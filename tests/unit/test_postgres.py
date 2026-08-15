from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError, DateOutOfRangeError
from quant_data._internal.shared.postgres import (
    DataQualityThresholdRow,
    DisagreementStatsRow,
    FieldGroupRow,
    FieldRow,
    IngestionCoverageRow,
    MaterialityFloorRow,
    PostgresDatabase,
    ProviderRow,
    StagingRow,
)
from quant_data.protocols import OHLCV, DataQuality, PendingResolutionBar, ProviderRole, RejectedWhistleblowerBar


class _FakeTransport:
    """Constructor-injected fake standing in for a real ConnectionTransport (rule 7: inject
    the dependency rather than monkeypatching quant-data's own transport code)."""

    def __init__(self, host: str = "localhost", port: int = 5433) -> None:
        self.host = host
        self.port = port
        self.closed = False

    def open(self) -> tuple[str, int]:
        return self.host, self.port

    def close(self) -> None:
        self.closed = True


class _FakeLogger:
    """Constructor-injected fake standing in for a host's own LoggingSink (quant-data#20)."""

    def __init__(self) -> None:
        self.info_calls: list[tuple[str, str]] = []
        self.perf_calls: list[tuple[str, float]] = []

    def diagnostic(self, message: str, category: str = "general") -> None:
        pass

    def info(self, message: str, category: str = "general") -> None:
        self.info_calls.append((message, category))

    def warning(self, message: str, category: str = "general") -> None:
        pass

    def error(self, message: str, category: str = "general") -> None:
        pass

    def fatal(self, message: str, category: str = "general") -> None:
        pass

    def perf(self, description: str, elapsed_seconds: float) -> None:
        self.perf_calls.append((description, elapsed_seconds))


def _connect(mock_psycopg, fetchone_results: list) -> MagicMock:
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = fetchone_results
    mock_psycopg.connect.return_value = mock_connection
    return mock_connection


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connect_pins_session_timezone_to_utc(mock_psycopg):
    # fact_market_data_1min.timestamp is TIMESTAMP WITHOUT TIME ZONE; without pinning the
    # session, Postgres implicitly casts our tz-aware UTC values down using the connection's
    # local TimeZone GUC, silently corrupting every stored timestamp (quant-data#9).
    PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["options"] == "-c TimeZone=UTC"


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connect_uses_transports_resolved_host_and_port(mock_psycopg):
    # PostgresDatabase must stay agnostic of how Postgres is actually reached -- it connects to
    # whatever the transport hands back from open(), not to any host/port of its own.
    PostgresDatabase(transport=_FakeTransport(host="tunnel-local", port=54321), user="quant_writer", password="x", dbname="quant_data")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["host"] == "tunnel-local"
    assert connect_kwargs["port"] == 54321


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connect_normalizes_localhost_to_literal_ipv4(mock_psycopg):
    # psycopg/libpq resolves the bare hostname "localhost" as dual-stack and can fall back from
    # an unreachable IPv6 loopback to IPv4 with a very long internal timeout (~130s observed in
    # practice) instead of connecting immediately -- see quant-data#19.
    PostgresDatabase(transport=_FakeTransport(host="localhost", port=5433), user="quant_writer", password="x", dbname="quant_data")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["host"] == "127.0.0.1"


@patch("quant_data._internal.shared.postgres.psycopg")
def test_injected_logger_receives_connect_info_and_perf_calls(mock_psycopg):
    # A host application can inject its own LoggingSink (quant-data#20) so quant-data's internal
    # logging lands in the host's own log stream instead of quant-data's private static Logger.
    logger = _FakeLogger()

    PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data", logger=logger)

    assert any("Connecting to Postgres" in message for message, _ in logger.info_calls)
    assert any("Connected to Postgres" in message for message, _ in logger.info_calls)
    perf_descriptions = [description for description, _ in logger.perf_calls]
    assert "transport.open()" in perf_descriptions
    assert "psycopg.connect()" in perf_descriptions


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connect_failure_closes_transport(mock_psycopg):
    mock_psycopg.Error = Exception
    mock_psycopg.connect.side_effect = mock_psycopg.Error("boom")
    transport = _FakeTransport()

    with pytest.raises(AppError):
        PostgresDatabase(transport=transport, user="quant_writer", password="x", dbname="quant_data")

    assert transport.closed is True


@patch("quant_data._internal.shared.postgres.psycopg")
def test_close_closes_connection_and_transport(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    transport = _FakeTransport()

    database = PostgresDatabase(transport=transport, user="quant_writer", password="x", dbname="quant_data")
    database.close()

    mock_connection.close.assert_called_once()
    assert transport.closed is True


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_bars_reports_perf_to_injected_logger(mock_psycopg):
    _connect(mock_psycopg, [(1,), (10,), (20,)])  # ticker_id, date_id, time_id
    logger = _FakeLogger()

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data", logger=logger)
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    database.write_bars([bar])

    write_bars_calls = [description for description, _ in logger.perf_calls if description.startswith("write_bars")]
    assert write_bars_calls == ["write_bars(1 bars)"]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_bars_commits_on_success(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), (10,), (20,)])  # ticker_id, date_id, time_id

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    written = database.write_bars([bar])

    assert written == 1
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_bars_rolls_back_on_missing_dim_date(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), None])  # ticker_id ok, date_id missing

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(DateOutOfRangeError):
        database.write_bars([bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_bars_rolls_back_on_missing_dim_time(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), (10,), None])  # ticker_id, date_id ok, time_id missing

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(AppError):
        database.write_bars([bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_staging_bars_commits_on_success(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(5,), (1,), (10,), (20,)])  # provider_id, ticker_id, date_id, time_id

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    written = database.write_staging_bars("ibkr", [bar])

    assert written == 1
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_staging_bars_raises_on_unknown_provider(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [None])  # no dim_provider row for this name

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(AppError):
        database.write_staging_bars("not-a-real-provider", [bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_staging_bars_rolls_back_on_missing_dim_date(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(5,), (1,), None])  # provider_id, ticker_id ok, date_id missing

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(DateOutOfRangeError):
        database.write_staging_bars("ibkr", [bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_staging_bars_lowercases_provider_name(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(5,), (1,), (10,), (20,)])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    database.write_staging_bars("IBKR", [bar])

    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    first_call_args = mock_cursor.execute.call_args_list[0].args
    assert first_call_args[1] == ("ibkr",)


@patch("quant_data._internal.shared.postgres.psycopg")
def test_write_staging_bars_reports_perf_to_injected_logger(mock_psycopg):
    _connect(mock_psycopg, [(5,), (1,), (10,), (20,)])
    logger = _FakeLogger()

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data", logger=logger)
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    database.write_staging_bars("ibkr", [bar])

    perf_descriptions = [description for description, _ in logger.perf_calls]
    assert "write_staging_bars(ibkr, 1 bars)" in perf_descriptions


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_dim_providers_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, "yfinance", "whistleblower"),
        (2, "ibkr", "candidate"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    providers = database.fetch_dim_providers()

    assert providers == [
        ProviderRow(provider_id=1, name="yfinance", role="whistleblower"),
        ProviderRow(provider_id=2, name="ibkr", role="candidate"),
    ]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_dim_field_groups_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, "ohlc"), (2, "volume")]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    field_groups = database.fetch_dim_field_groups()

    assert field_groups == [
        FieldGroupRow(field_group_id=1, name="ohlc"),
        FieldGroupRow(field_group_id=2, name="volume"),
    ]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_dim_fields_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, "open"),
        (2, "high"),
        (3, "low"),
        (4, "close"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    fields = database.fetch_dim_fields()

    assert fields == [
        FieldRow(field_id=1, name="open"),
        FieldRow(field_id=2, name="high"),
        FieldRow(field_id=3, name="low"),
        FieldRow(field_id=4, name="close"),
    ]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_provider_pair_disagreement_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(2, 1, 3, 100, 0.0, 0.000064)]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    stats = database.fetch_provider_pair_disagreement()

    assert stats == [DisagreementStatsRow(provider_id=2, ticker_id=1, field_id=3, sample_count=100, running_mean=0.0, running_m2=0.000064)]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_ingestion_coverage_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, 2, 10, 14)]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    coverage = database.fetch_ingestion_coverage()

    assert coverage == [IngestionCoverageRow(ticker_id=1, provider_id=2, start_date_id=10, end_date_id=14)]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_data_quality_thresholds_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(2, 1, 3.0, 6.0, 4.0, 8.0)]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    thresholds = database.fetch_data_quality_thresholds()

    assert thresholds == [DataQualityThresholdRow(provider_id=2, ticker_id=1, k_reversal_oc=3.0, k_trend_oc=6.0, k_reversal_hl=4.0, k_trend_hl=8.0)]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_materiality_floors_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(2, 1, 4, 0.05, "absolute")]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    floors = database.fetch_materiality_floors()

    assert floors == [MaterialityFloorRow(provider_id=2, ticker_id=1, field_id=4, floor_value=0.05, floor_type="absolute")]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_whistleblower_accepted_staging_rows_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, 10, 20, datetime(2026, 7, 24, 13, 30), 2, 100.0, 101.0, 99.0, 100.5, 1000, "accepted"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_reader", password="", dbname="quant_data")

    rows = database.fetch_whistleblower_accepted_staging_rows()

    assert rows == [
        StagingRow(
            ticker_id=1,
            date_id=10,
            time_id=20,
            timestamp=datetime(2026, 7, 24, 13, 30),
            provider_id=2,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            data_quality="accepted",
        )
    ]
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    call_args = mock_cursor.execute.call_args
    assert call_args.args[1] == ("whistleblower", "accepted")


@patch("quant_data._internal.shared.postgres.psycopg")
def test_mark_staging_bars_rejected_commits_once_for_multiple_keys(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.mark_staging_bars_rejected([(2, 1, 10, 20), (2, 1, 10, 21), (2, 1, 10, 22)])

    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()
    assert mock_connection.cursor.return_value.__enter__.return_value.execute.call_count == 3


@patch("quant_data._internal.shared.postgres.psycopg")
def test_mark_staging_bars_rejected_empty_keys_is_a_noop(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.mark_staging_bars_rejected([])

    mock_connection.commit.assert_not_called()
    mock_connection.cursor.return_value.__enter__.return_value.execute.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_staging_rows_for_reconciliation_scopes_by_provider_names(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, 10, 20, datetime(2026, 7, 24, 13, 30), 2, 100.0, 101.0, 99.0, 100.5, 1000, "accepted"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    rows = database.fetch_staging_rows_for_reconciliation(["yfinance", "ibkr"], ["ibkr"])

    assert rows == [
        StagingRow(
            ticker_id=1,
            date_id=10,
            time_id=20,
            timestamp=datetime(2026, 7, 24, 13, 30),
            provider_id=2,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            data_quality="accepted",
        )
    ]
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    call_args = mock_cursor.execute.call_args
    assert call_args.args[1]["names"] == ["yfinance", "ibkr"]
    assert call_args.args[1]["required_names"] == ["ibkr"]
    assert call_args.args[1]["required_count"] == 1


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_resolved_field_groups_returns_winning_provider_by_bar(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, 10, 20, 1, 2)]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    resolved = database.fetch_resolved_field_groups()

    assert resolved == {(1, 10, 20, 1): 2}


@patch("quant_data._internal.shared.postgres.psycopg")
def test_record_reconciliation_inserts_fact_and_participant_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.record_reconciliation(1, 10, 20, 1, 2, "agreement", [(2, True), (1, False)])

    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    assert mock_cursor.execute.call_count == 3  # 1 fact_reconciliation insert + 2 participant inserts
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_record_reconciliation_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    with pytest.raises(AppError):
        database.record_reconciliation(1, 10, 20, 1, 2, "agreement", [(2, True)])

    mock_connection.rollback.assert_called_once()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_promote_bar_to_fact_upserts_fact_only(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.promote_bar_to_fact(1, 10, 20, datetime(2026, 7, 24, 13, 30), 100.0, 101.0, 99.0, 100.5, 1000, "accepted")

    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    assert mock_cursor.execute.call_count == 1  # fact upsert only -- purge is a separate call
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_purge_staging_bar_with_no_candidate_rows_only_selects_and_deletes(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = []

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.purge_staging_bar(1, 10, 20)

    assert mock_cursor.execute.call_count == 2  # select candidates, then delete -- nothing to archive
    mock_connection.commit.assert_called_once()

    # Whistleblower-role providers must be excluded from both the select and the delete
    # (croicu/quant-data#28) -- guards against an accidental revert to an unconditional delete.
    for call in mock_cursor.execute.call_args_list:
        assert call.args[1] == (1, 10, 20, ProviderRole.WHISTLEBLOWER.value)


@patch("quant_data._internal.shared.postgres.psycopg")
def test_purge_staging_bar_archives_candidate_rows_before_deleting(mock_psycopg):
    # croicu/quant-data#35: archive-then-delete -- each selected candidate row is inserted into
    # market_data_archive (RETURNING archive_id), that archive_id is written back onto the
    # provider's fact_reconciliation_participant row, and only then is the staging row deleted.
    mock_connection = _connect(mock_psycopg, [(501,), (502,)])
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [
        (2, datetime(2026, 7, 24, 13, 30), 100.0, 101.0, 99.0, 100.5, 1000, "accepted"),
        (3, datetime(2026, 7, 24, 13, 30), 100.1, 101.1, 99.1, 100.6, 1001, "accepted"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.purge_staging_bar(1, 10, 20)

    mock_connection.commit.assert_called_once()
    calls = mock_cursor.execute.call_args_list
    assert len(calls) == 1 + 2 * 2 + 1  # select + (archive insert + participant update) * 2 rows + delete

    insert_calls = [call for call in calls if "INSERT INTO market_data_archive" in call.args[0]]
    assert [call.args[1][0] for call in insert_calls] == [2, 3]  # provider_id, in select order

    update_calls = [call for call in calls if "UPDATE fact_reconciliation_participant" in call.args[0]]
    assert [call.args[1] for call in update_calls] == [(501, 1, 10, 20, 2), (502, 1, 10, 20, 3)]

    delete_calls = [call for call in calls if "DELETE FROM staging_market_data_1min" in call.args[0]]
    assert len(delete_calls) == 1


@patch("quant_data._internal.shared.postgres.psycopg")
def test_purge_staging_bar_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    with pytest.raises(AppError):
        database.purge_staging_bar(1, 10, 20)

    mock_connection.rollback.assert_called_once()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_save_provider_pair_disagreement_batch_upserts(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.save_provider_pair_disagreement_batch([(2, 1, 3, 101, 0.001, 0.00007, 0.00083)])

    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()

    call_args = mock_connection.cursor.return_value.__enter__.return_value.execute.call_args
    assert call_args.args[1] == (2, 1, 3, 101, 0.001, 0.00007, 0.00083)


@patch("quant_data._internal.shared.postgres.psycopg")
def test_save_provider_pair_disagreement_batch_commits_once_for_multiple_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.save_provider_pair_disagreement_batch(
        [
            (2, 1, 3, 101, 0.001, 0.00007, 0.00083),
            (2, 1, 4, 101, 0.002, 0.00008, 0.00091),
            (2, 1, 5, 101, 0.003, 0.00009, 0.00095),
            (2, 1, 6, 101, 0.004, 0.00010, 0.00100),
        ]
    )

    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()
    assert mock_connection.cursor.return_value.__enter__.return_value.execute.call_count == 4


@patch("quant_data._internal.shared.postgres.psycopg")
def test_save_provider_pair_disagreement_batch_empty_rows_is_a_noop(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.save_provider_pair_disagreement_batch([])

    mock_connection.commit.assert_not_called()
    mock_connection.cursor.return_value.__enter__.return_value.execute.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_dataset_inception_date_returns_the_date(mock_psycopg):
    _connect(mock_psycopg, [(date(2020, 1, 1),)])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    assert database.fetch_dataset_inception_date() == date(2020, 1, 1)


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_dataset_inception_date_raises_when_table_is_empty(mock_psycopg):
    _connect(mock_psycopg, [None])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    with pytest.raises(AppError):
        database.fetch_dataset_inception_date()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_earliest_covered_date_returns_the_date(mock_psycopg):
    _connect(mock_psycopg, [(date(2026, 1, 15),)])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    assert database.fetch_earliest_covered_date("aapl") == date(2026, 1, 15)

    mock_cursor = mock_psycopg.connect.return_value.cursor.return_value.__enter__.return_value
    call_args = mock_cursor.execute.call_args
    assert call_args.args[1] == ("AAPL", "AAPL")


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_earliest_covered_date_returns_none_for_a_never_ingested_ticker(mock_psycopg):
    _connect(mock_psycopg, [(None,)])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    assert database.fetch_earliest_covered_date("aapl") is None


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_pending_manual_resolution_staging_rows_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, 10, 20, datetime(2026, 7, 24, 13, 30), 2, 100.0, 101.0, 99.0, 100.5, 1000, "accepted"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    rows = database.fetch_pending_manual_resolution_staging_rows()

    assert rows == [
        StagingRow(
            ticker_id=1,
            date_id=10,
            time_id=20,
            timestamp=datetime(2026, 7, 24, 13, 30),
            provider_id=2,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            data_quality="accepted",
        )
    ]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_pending_resolution_bars_returns_one_candidate_per_provider(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("SPY", datetime(2026, 8, 3, 13, 30), "ohlc", "ibkr", "candidate", 100.0, 101.0, 99.0, 100.5, 1000, "accepted"),
        ("SPY", datetime(2026, 8, 3, 13, 30), "ohlc", "yfinance", "whistleblower", 100.1, 101.1, 99.1, 100.6, 1010, "accepted"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_reader", password="", dbname="quant_data")

    candidates = database.fetch_pending_resolution_bars("spy", date(2026, 8, 3), date(2026, 8, 3))

    assert candidates == [
        PendingResolutionBar(
            field_group="ohlc",
            provider="ibkr",
            role=ProviderRole.CANDIDATE,
            bar=OHLCV(
                ticker="SPY",
                timestamp=datetime(2026, 8, 3, 13, 30),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000,
                data_quality=DataQuality.ACCEPTED,
            ),
        ),
        PendingResolutionBar(
            field_group="ohlc",
            provider="yfinance",
            role=ProviderRole.WHISTLEBLOWER,
            bar=OHLCV(
                ticker="SPY",
                timestamp=datetime(2026, 8, 3, 13, 30),
                open=100.1,
                high=101.1,
                low=99.1,
                close=100.6,
                volume=1010,
                data_quality=DataQuality.ACCEPTED,
            ),
        ),
    ]


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_rejected_whistleblower_bars_returns_rows(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("SPY", datetime(2026, 8, 3, 13, 30), "yfinance", 100.1, 101.1, 99.1, 100.6, 1010, "rejected"),
    ]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_reader", password="", dbname="quant_data")

    rejected = database.fetch_rejected_whistleblower_bars("spy", date(2026, 8, 3), date(2026, 8, 3))

    assert rejected == [
        RejectedWhistleblowerBar(
            provider="yfinance",
            bar=OHLCV(
                ticker="SPY",
                timestamp=datetime(2026, 8, 3, 13, 30),
                open=100.1,
                high=101.1,
                low=99.1,
                close=100.6,
                volume=1010,
                data_quality=DataQuality.REJECTED,
            ),
        ),
    ]
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    call_args = mock_cursor.execute.call_args
    assert call_args.args[1] == ("SPY", date(2026, 8, 3), date(2026, 8, 3), "whistleblower", "rejected")


@patch("quant_data._internal.shared.postgres.psycopg")
def test_fetch_pending_manual_resolution_keys_returns_keys(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, 10, 20, 1), (1, 10, 21, 1)]

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    keys = database.fetch_pending_manual_resolution_keys()

    assert keys == {(1, 10, 20, 1), (1, 10, 21, 1)}


@patch("quant_data._internal.shared.postgres.psycopg")
def test_mark_pending_manual_resolution_inserts(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.mark_pending_manual_resolution(1, 10, 20, 1, datetime(2026, 7, 24, 13, 30))

    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_mark_pending_manual_resolution_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    with pytest.raises(AppError):
        database.mark_pending_manual_resolution(1, 10, 20, 1, datetime(2026, 7, 24, 13, 30))

    mock_connection.rollback.assert_called_once()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_clear_pending_manual_resolution_deletes(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    database.clear_pending_manual_resolution(1, 10, 20, 1)

    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.postgres.psycopg")
def test_clear_pending_manual_resolution_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = PostgresDatabase(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_data")

    with pytest.raises(AppError):
        database.clear_pending_manual_resolution(1, 10, 20, 1)

    mock_connection.rollback.assert_called_once()
