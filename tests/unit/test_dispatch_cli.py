from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dispatch import cli
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule import JobRow
from quant_data._internal.shared.settings import Settings
from tests.mocks.dispatch_database import FakeDispatchDatabase

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"

_NOW = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)


def _use_database(database: FakeDispatchDatabase):
    def factory(postgres_settings):
        return database

    return factory


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_main_runs_a_due_job_and_records_success():
    job = JobRow(job_id=1, name="ingest", command=["quant-ingest", "--catch-up"], interval_seconds=3600)
    database = FakeDispatchDatabase(jobs=[job])
    runs: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess:
        runs.append(command)
        return _completed(0)

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=runner,
        now=lambda: _NOW,
    )

    assert exit_code == 0
    assert runs == [["quant-ingest", "--catch-up"]]
    assert database.running_job_ids == [1]
    assert database.recorded_results == [(1, 0, None, datetime(2026, 1, 2, 13, 0, 0, tzinfo=timezone.utc), False)]
    assert database.closed is True


def test_main_disables_a_run_once_job_on_success_instead_of_rescheduling():
    job = JobRow(job_id=4, name="workitem-ingest", command=["quant-ingest"], interval_seconds=300, run_once=True)
    database = FakeDispatchDatabase(jobs=[job])

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=lambda command: _completed(0),
        now=lambda: _NOW,
    )

    assert exit_code == 0
    job_id, exit_code_recorded, error_message, _next_run_at, disable = database.recorded_results[0]
    assert (job_id, exit_code_recorded, error_message, disable) == (4, 0, None, True)


def test_main_retries_a_run_once_job_on_failure_instead_of_disabling():
    job = JobRow(job_id=5, name="workitem-ingest", command=["quant-ingest"], interval_seconds=300, run_once=True)
    database = FakeDispatchDatabase(jobs=[job])

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=lambda command: _completed(1, stderr="boom"),
        now=lambda: _NOW,
    )

    assert exit_code == 1
    job_id, exit_code_recorded, error_message, _next_run_at, disable = database.recorded_results[0]
    assert (job_id, exit_code_recorded, error_message, disable) == (5, 1, "boom", False)


def test_main_returns_one_when_a_job_fails():
    job = JobRow(job_id=2, name="reconcile", command=["quant-reconcile"], interval_seconds=1800)
    database = FakeDispatchDatabase(jobs=[job])

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=lambda command: _completed(1, stderr="boom"),
        now=lambda: _NOW,
    )

    assert exit_code == 1
    job_id, exit_code_recorded, error_message, next_run_at, disable = database.recorded_results[0]
    assert (job_id, exit_code_recorded, error_message, disable) == (2, 1, "boom", False)
    assert next_run_at == datetime(2026, 1, 2, 12, 30, 0, tzinfo=timezone.utc)


def test_main_records_launch_failure_as_exit_code_negative_one():
    job = JobRow(job_id=3, name="stage", command=["quant-stage"], interval_seconds=600)
    database = FakeDispatchDatabase(jobs=[job])

    def runner(command: list[str]) -> subprocess.CompletedProcess:
        raise FileNotFoundError("quant-stage: not found")

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=runner,
        now=lambda: _NOW,
    )

    assert exit_code == 1
    job_id, exit_code_recorded, error_message, _next_run_at, disable = database.recorded_results[0]
    assert (job_id, exit_code_recorded, disable) == (3, -1, False)
    assert "not found" in error_message


def test_main_dispatches_multiple_due_jobs_independently():
    jobs = [
        JobRow(job_id=1, name="ingest", command=["quant-ingest"], interval_seconds=3600),
        JobRow(job_id=2, name="stage", command=["quant-stage"], interval_seconds=3600),
    ]
    database = FakeDispatchDatabase(jobs=jobs)
    results = {"quant-ingest": 0, "quant-stage": 1}

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=lambda command: _completed(results[command[0]]),
        now=lambda: _NOW,
    )

    assert exit_code == 1  # one job failed
    assert database.running_job_ids == [1, 2]
    assert len(database.recorded_results) == 2


def test_default_database_factory_raises_when_worker_not_configured():
    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._default_database_factory(settings.postgres)


def test_resolve_executable_prefers_the_venv_sibling_when_present(tmp_path):
    (tmp_path / "quant-ingest").write_text("#!/bin/sh\n", encoding="utf-8")

    resolved = cli._resolve_executable("quant-ingest", tmp_path)

    assert resolved == str(tmp_path / "quant-ingest")


def test_resolve_executable_falls_back_to_bare_name_when_not_a_venv_sibling(tmp_path):
    resolved = cli._resolve_executable("psql", tmp_path)

    assert resolved == "psql"


def test_main_returns_zero_when_nothing_is_due():
    database = FakeDispatchDatabase(jobs=[])

    exit_code = cli.main(
        [],
        settings_path=SETTINGS_PATH,
        database_factory=_use_database(database),
        subprocess_runner=lambda command: _completed(0),
        now=lambda: _NOW,
    )

    assert exit_code == 0
    assert database.recorded_results == []
    assert database.closed is True
