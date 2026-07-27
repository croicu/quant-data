from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from quant_data.defs.protocols import OHLCV
from quant_data.shared.errors import AppError, DateOutOfRangeError
from quant_data.shared.postgres import PostgresDatabase


def _connect(mock_psycopg, fetchone_results: list) -> MagicMock:
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.side_effect = fetchone_results
    mock_psycopg.connect.return_value = mock_connection
    return mock_connection


@patch("quant_data.shared.postgres.psycopg")
def test_write_bars_commits_on_success(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), (10,), (20,)])  # ticker_id, date_id, time_id

    database = PostgresDatabase(host="localhost", port=5433, user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    written = database.write_bars([bar])

    assert written == 1
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data.shared.postgres.psycopg")
def test_write_bars_rolls_back_on_missing_dim_date(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), None])  # ticker_id ok, date_id missing

    database = PostgresDatabase(host="localhost", port=5433, user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(DateOutOfRangeError):
        database.write_bars([bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data.shared.postgres.psycopg")
def test_write_bars_rolls_back_on_missing_dim_time(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [(1,), (10,), None])  # ticker_id, date_id ok, time_id missing

    database = PostgresDatabase(host="localhost", port=5433, user="quant_writer", password="x", dbname="quant_data")
    bar = OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)

    with pytest.raises(AppError):
        database.write_bars([bar])

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()
