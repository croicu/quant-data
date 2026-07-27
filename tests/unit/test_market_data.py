from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

from client.market_data import MarketData


@patch("shared.postgres.psycopg")
def test_fetch_bars_delegates_to_postgres_database(mock_psycopg):
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [("AAPL", datetime(2026, 7, 24, 13, 30), 1.0, 2.0, 0.5, 1.5, 100, False)]
    mock_psycopg.connect.return_value = mock_connection

    client = MarketData(host="localhost", port=5433, dbname="quant_data")
    bars = client.fetch_bars("aapl", date(2026, 7, 24), date(2026, 7, 24))

    assert len(bars) == 1
    assert bars[0].ticker == "AAPL"
    assert bars[0].volume == 100


@patch("shared.postgres.psycopg")
def test_connects_as_quant_reader_by_default(mock_psycopg):
    MarketData(host="localhost", port=5433, dbname="quant_data")

    mock_psycopg.connect.assert_called_once_with(host="localhost", port=5433, user="quant_reader", password="", dbname="quant_data")


@patch("shared.postgres.psycopg")
def test_close_delegates_to_postgres_database(mock_psycopg):
    mock_connection = MagicMock()
    mock_psycopg.connect.return_value = mock_connection

    client = MarketData(host="localhost", port=5433, dbname="quant_data")
    client.close()

    mock_connection.close.assert_called_once()


def test_has_no_write_method():
    assert not hasattr(MarketData, "write_bars")
