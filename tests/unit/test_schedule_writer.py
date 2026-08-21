from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule_writer import NewJob, WorkItemScheduleWriter


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


def _connect(mock_psycopg, job_ids: list[int]) -> tuple[MagicMock, MagicMock]:
    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [(job_id,) for job_id in job_ids]
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_psycopg.connect.return_value = mock_connection
    mock_psycopg.Error = Exception
    mock_psycopg.errors.UniqueViolation = type("UniqueViolation", (Exception,), {})
    return mock_connection, mock_cursor


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_connect_pins_session_timezone_to_utc(mock_psycopg):
    _connect(mock_psycopg, [])

    WorkItemScheduleWriter(transport=_FakeTransport(), user="quant_scheduler", password="x", dbname="quant_schedule")

    _, connect_kwargs = mock_psycopg.connect.call_args
    assert connect_kwargs["options"] == "-c TimeZone=UTC"
    assert connect_kwargs["dbname"] == "quant_schedule"


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_connect_wraps_failure_and_closes_transport(mock_psycopg):
    mock_psycopg.connect.side_effect = Exception("connection refused")
    mock_psycopg.Error = Exception
    transport = _FakeTransport()

    with pytest.raises(AppError):
        WorkItemScheduleWriter(transport=transport, user="quant_scheduler", password="x", dbname="quant_schedule")

    assert transport.closed is True


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_close_closes_connection_and_transport(mock_psycopg):
    mock_connection, _ = _connect(mock_psycopg, [])
    transport = _FakeTransport()

    writer = WorkItemScheduleWriter(transport=transport, user="quant_scheduler", password="x", dbname="quant_schedule")
    writer.close()

    mock_connection.close.assert_called_once()
    assert transport.closed is True


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_create_jobs_inserts_in_order_and_resolves_dependencies(mock_psycopg):
    now = datetime(2026, 8, 20, 12, 0, 0)
    mock_connection, mock_cursor = _connect(mock_psycopg, [10, 11, 12])

    writer = WorkItemScheduleWriter(transport=_FakeTransport(), user="quant_scheduler", password="x", dbname="quant_schedule")

    jobs = [
        NewJob(name="ingest-a", command=["quant-ingest"], interval_seconds=300, next_run_at=now, run_once=True),
        NewJob(name="ingest-b", command=["quant-ingest"], interval_seconds=300, next_run_at=now, run_once=True),
        NewJob(
            name="stage",
            command=["quant-stage"],
            interval_seconds=300,
            next_run_at=now,
            run_once=True,
            depends_on_names=["ingest-a", "ingest-b"],
        ),
    ]

    name_to_id = writer.create_jobs(jobs)

    assert name_to_id == {"ingest-a": 10, "ingest-b": 11, "stage": 12}
    mock_connection.commit.assert_called_once()
    mock_connection.rollback.assert_not_called()

    dependency_inserts = [call.args[1] for call in mock_cursor.execute.call_args_list if "job_dependencies" in call.args[0]]
    assert len(dependency_inserts) == 2
    assert set(dependency_inserts) == {(12, 10), (12, 11)}


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_create_jobs_raises_on_dependency_not_yet_created(mock_psycopg):
    now = datetime(2026, 8, 20, 12, 0, 0)
    _connect(mock_psycopg, [10])

    writer = WorkItemScheduleWriter(transport=_FakeTransport(), user="quant_scheduler", password="x", dbname="quant_schedule")

    jobs = [
        NewJob(
            name="stage",
            command=["quant-stage"],
            interval_seconds=300,
            next_run_at=now,
            run_once=True,
            depends_on_names=["ingest-not-yet-created"],
        ),
    ]

    with pytest.raises(AppError):
        writer.create_jobs(jobs)


@patch("quant_data._internal.shared.schedule_writer.psycopg")
def test_create_jobs_rolls_back_and_wraps_unique_violation(mock_psycopg):
    now = datetime(2026, 8, 20, 12, 0, 0)
    mock_connection, mock_cursor = _connect(mock_psycopg, [])
    mock_cursor.execute.side_effect = mock_psycopg.errors.UniqueViolation("duplicate key")

    writer = WorkItemScheduleWriter(transport=_FakeTransport(), user="quant_scheduler", password="x", dbname="quant_schedule")

    jobs = [NewJob(name="ingest-a", command=["quant-ingest"], interval_seconds=300, next_run_at=now, run_once=True)]

    with pytest.raises(AppError):
        writer.create_jobs(jobs)

    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()
