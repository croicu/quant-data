from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule import JobRow, ScheduleDatabase


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


def _connect(mock_psycopg, fetchall_result: list) -> MagicMock:
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fetchall_result
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_psycopg.connect.return_value = mock_connection
    return mock_connection


@patch("quant_data._internal.shared.schedule.psycopg")
def test_connect_pins_session_timezone_to_utc(mock_psycopg):
    _connect(mock_psycopg, [])

    ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["options"] == "-c TimeZone=UTC"
    assert connect_kwargs["dbname"] == "quant_schedule"


@patch("quant_data._internal.shared.schedule.psycopg")
def test_connect_wraps_failure_and_closes_transport(mock_psycopg):
    mock_psycopg.connect.side_effect = Exception("connection refused")
    mock_psycopg.Error = Exception
    transport = _FakeTransport()

    with pytest.raises(AppError):
        ScheduleDatabase(transport=transport, user="quant_worker", password="x", dbname="quant_schedule")

    assert transport.closed is True


@patch("quant_data._internal.shared.schedule.psycopg")
def test_close_closes_connection_and_transport(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    transport = _FakeTransport()

    database = ScheduleDatabase(transport=transport, user="quant_worker", password="x", dbname="quant_schedule")
    database.close()

    mock_connection.close.assert_called_once()
    assert transport.closed is True


@patch("quant_data._internal.shared.schedule.psycopg")
def test_fetch_due_jobs_returns_rows(mock_psycopg):
    _connect(mock_psycopg, [(1, "ingest", ["quant-ingest", "--catch-up"], 3600)])

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")

    due_jobs = database.fetch_due_jobs(datetime(2026, 7, 24, 13, 0))

    assert due_jobs == [JobRow(job_id=1, name="ingest", command=["quant-ingest", "--catch-up"], interval_seconds=3600)]


@patch("quant_data._internal.shared.schedule.psycopg")
def test_fetch_due_jobs_raises_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")

    with pytest.raises(AppError):
        database.fetch_due_jobs(datetime(2026, 7, 24, 13, 0))


@patch("quant_data._internal.shared.schedule.psycopg")
def test_mark_job_running_commits(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")
    database.mark_job_running(7)

    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    assert mock_cursor.execute.call_args.args[1] == (7,)
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.schedule.psycopg")
def test_mark_job_running_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")

    with pytest.raises(AppError):
        database.mark_job_running(7)

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()


@patch("quant_data._internal.shared.schedule.psycopg")
def test_record_job_result_commits_with_expected_values(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")
    next_run_at = datetime(2026, 7, 24, 14, 0)

    database.record_job_result(7, 1, "boom", next_run_at)

    mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
    assert mock_cursor.execute.call_args.args[1] == (1, "boom", next_run_at, 7)
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()


@patch("quant_data._internal.shared.schedule.psycopg")
def test_record_job_result_rolls_back_on_error(mock_psycopg):
    mock_connection = _connect(mock_psycopg, [])
    mock_psycopg.Error = Exception
    mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = mock_psycopg.Error("boom")

    database = ScheduleDatabase(transport=_FakeTransport(), user="quant_worker", password="x", dbname="quant_schedule")

    with pytest.raises(AppError):
        database.record_job_result(7, 0, None, datetime(2026, 7, 24, 14, 0))

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()
