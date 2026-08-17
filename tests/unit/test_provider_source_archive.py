from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.contracts import PayloadKind
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.provider_source_archive import ProviderSourceArchiveWriter


class _FakeTransport:
    """Constructor-injected fake standing in for a real ConnectionTransport (rule 7: inject the
    dependency rather than monkeypatching quant-data's own transport code)."""

    def __init__(self, host: str = "localhost", port: int = 5433) -> None:
        self.host = host
        self.port = port
        self.closed = False

    def open(self) -> tuple[str, int]:
        return self.host, self.port

    def close(self) -> None:
        self.closed = True


def _connect(mock_psycopg) -> MagicMock:
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_psycopg.connect.return_value = mock_connection
    return mock_connection


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_connect_pins_session_timezone_to_utc(mock_psycopg):
    _connect(mock_psycopg)

    ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["options"] == "-c TimeZone=UTC"
    assert connect_kwargs["dbname"] == "quant_ingest"


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_connect_wraps_failure_and_closes_transport(mock_psycopg):
    mock_psycopg.connect.side_effect = Exception("connection refused")
    mock_psycopg.Error = Exception
    transport = _FakeTransport()

    with pytest.raises(AppError):
        ProviderSourceArchiveWriter(transport=transport, user="quant_writer", password="x", dbname="quant_ingest")

    assert transport.closed is True


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_close_closes_connection_and_transport(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    transport = _FakeTransport()

    writer = ProviderSourceArchiveWriter(transport=transport, user="quant_writer", password="x", dbname="quant_ingest")
    writer.close()

    mock_connection.close.assert_called_once()
    assert transport.closed is True


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_inserts_archive_row_and_commits(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = []  # no existing coverage range touches this date

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(
        ticker="spy",
        provider="MASSIVE",
        trading_date=date(2026, 7, 23),
        fetch_version="1",
        payload_kind=PayloadKind.RAW_API_RESPONSE,
        payload={"status": "OK"},
    )

    insert_call = mock_cursor.execute.call_args_list[0]
    assert "INSERT INTO provider_source_archive" in insert_call.args[0]
    assert insert_call.args[1] == ("SPY", "massive", date(2026, 7, 23), "1", "raw_api_response", '{"status": "OK"}')
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_inserts_new_coverage_range_when_nothing_touches(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = []

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="1", payload_kind=PayloadKind.PARSED_BARS, payload={})

    coverage_insert = mock_cursor.execute.call_args_list[-1]
    assert "INSERT INTO archive_coverage" in coverage_insert.args[0]
    assert coverage_insert.args[1] == ("SPY", "massive", "1", date(2026, 7, 23), date(2026, 7, 23))
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_extends_one_adjacent_coverage_range(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [(99, date(2026, 7, 20), date(2026, 7, 22))]

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="1", payload_kind=PayloadKind.PARSED_BARS, payload={})

    update_call = mock_cursor.execute.call_args_list[-1]
    assert "UPDATE archive_coverage" in update_call.args[0]
    assert update_call.args[1] == (date(2026, 7, 20), date(2026, 7, 23), 99)
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_is_a_no_op_for_coverage_when_date_already_covered(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [(99, date(2026, 7, 20), date(2026, 7, 25))]

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="1", payload_kind=PayloadKind.PARSED_BARS, payload={})

    for call in mock_cursor.execute.call_args_list:
        sql = call.args[0]
        assert "UPDATE archive_coverage" not in sql
        assert "DELETE FROM archive_coverage" not in sql
        assert "INSERT INTO archive_coverage" not in sql
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_merges_two_bridged_coverage_ranges(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [(97, date(2026, 7, 20), date(2026, 7, 22)), (98, date(2026, 7, 24), date(2026, 7, 26))]

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="1", payload_kind=PayloadKind.PARSED_BARS, payload={})

    calls = mock_cursor.execute.call_args_list
    delete_calls = []
    update_calls = []
    for call in calls:
        sql = call.args[0]
        if "DELETE FROM archive_coverage" in sql:
            delete_calls.append(call)
        elif "UPDATE archive_coverage" in sql:
            update_calls.append(call)
    assert len(delete_calls) == 1
    assert delete_calls[0].args[1] == (98,)
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == (date(2026, 7, 20), date(2026, 7, 26), 97)
    mock_connection.commit.assert_called_once()


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_keys_coverage_by_fetch_version_separately(mock_psycopg):
    # A bumped fetch_version must not silently extend an older version's range -- the SELECT
    # itself is scoped by fetch_version, so a real (non-mocked) database would simply never return
    # the old-version row here; asserting the query includes it is enough to guard the intent.
    mock_connection = _connect(mock_psycopg)
    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = []

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")
    writer.record_fetch(ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="2", payload_kind=PayloadKind.PARSED_BARS, payload={})

    select_call = mock_cursor.execute.call_args_list[1]
    assert "fetch_version = %s" in select_call.args[0]
    assert select_call.args[1] == ("SPY", "massive", "2", date(2026, 7, 23), date(2026, 7, 23))


@patch("quant_data._internal.shared.provider_source_archive.psycopg")
def test_record_fetch_rolls_back_and_wraps_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg)
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    writer = ProviderSourceArchiveWriter(transport=_FakeTransport(), user="quant_writer", password="x", dbname="quant_ingest")

    with pytest.raises(AppError):
        writer.record_fetch(
            ticker="spy", provider="massive", trading_date=date(2026, 7, 23), fetch_version="1", payload_kind=PayloadKind.PARSED_BARS, payload={}
        )

    mock_connection.rollback.assert_called_once()
