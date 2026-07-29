from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError, DateOutOfRangeError
from quant_data._internal.shared.postgres import PostgresDatabase
from quant_data.protocols import OHLCV


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
