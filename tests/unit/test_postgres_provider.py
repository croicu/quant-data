from __future__ import annotations

from unittest.mock import patch

from quant_data.client.postgres_provider import create_postgres_provider


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connects_as_quant_reader_by_default(mock_psycopg):
    create_postgres_provider(host="localhost", port=5433, dbname="quant_data")

    mock_psycopg.connect.assert_called_once_with(host="localhost", port=5433, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC")
