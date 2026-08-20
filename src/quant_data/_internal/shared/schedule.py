from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import psycopg

from quant_data._internal.contracts import ConnectionTransport
from quant_data.protocols import LoggingSink

from .diagnostics import Logger
from .errors import AppError

CATEGORY_SCHEDULE = "schedule"


@dataclass
class JobRow:
    job_id: int
    name: str
    command: list[str]
    interval_seconds: int


class ScheduleDatabase:
    """Client for the quant_schedule database -- the table-driven schedule quant-dispatch reads
    (croicu/quant-data#66). Deliberately a separate class from PostgresDatabase, not a method on
    it: quant_schedule has no relationship to quant_data's star schema (Postgres has no
    cross-database foreign keys) and is meant to eventually schedule jobs across other databases
    this repo doesn't own (a future quant_trades, quant_accounting, ...), so it owns its own
    connection rather than living inside quant_data's. Connects as quant_worker (read/update only)
    -- a separate quant_scheduler role (read/insert/update) exists for hand-managing job
    definitions via psql, but no code in this repo ever authenticates as it, matching the "ships
    empty, inserted by hand" precedent already set for jobs itself. This class accordingly never
    issues an INSERT.
    """

    def __init__(self, transport: ConnectionTransport, user: str, password: str, dbname: str, logger: LoggingSink = Logger) -> None:
        self._transport = transport
        self._logger = logger

        open_started = time.perf_counter()
        effective_host, effective_port = transport.open()
        self._logger.perf("transport.open()", time.perf_counter() - open_started)

        if effective_host == "localhost":
            # Same libpq dual-stack pitfall as PostgresDatabase -- see quant-data#19.
            effective_host = "127.0.0.1"

        self._logger.info(f"Connecting to quant_schedule at {effective_host}:{effective_port}/{dbname} as '{user}'...", category=CATEGORY_SCHEDULE)

        connect_started = time.perf_counter()
        try:
            self._connection = psycopg.connect(
                host=effective_host,
                port=effective_port,
                user=user,
                password=password,
                dbname=dbname,
                options="-c TimeZone=UTC",
            )
        except psycopg.Error as error:
            transport.close()
            raise AppError(f"Failed to connect to quant_schedule at {effective_host}:{effective_port}/{dbname}: {error}") from error
        self._logger.perf("psycopg.connect()", time.perf_counter() - connect_started)

        self._logger.info(f"Connected to quant_schedule at {effective_host}:{effective_port}/{dbname}.", category=CATEGORY_SCHEDULE)

    def close(self) -> None:
        self._connection.close()
        self._transport.close()

    def fetch_due_jobs(self, now: datetime) -> list[JobRow]:
        """Every enabled, currently-idle job whose next_run_at has arrived. status = 'idle'
        excludes a job whose previous invocation hasn't finished yet (see mark_job_running/
        record_job_result), so a dispatcher run overlapping a still-running prior one skips it
        instead of double-dispatching."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id, name, command, interval_seconds
                    FROM jobs
                    WHERE enabled AND status = 'idle' AND next_run_at <= %s
                    ORDER BY next_run_at
                    """,
                    (now,),
                )
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch due jobs: {error}") from error

        due_jobs: list[JobRow] = []
        for job_id, name, command, interval_seconds in rows:
            due_jobs.append(JobRow(job_id=job_id, name=name, command=list(command), interval_seconds=interval_seconds))
        return due_jobs

    def mark_job_running(self, job_id: int) -> None:
        """Flips a job to 'running' immediately before its subprocess is launched, so a concurrent
        quant-dispatch invocation's fetch_due_jobs excludes it until record_job_result flips it
        back."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("UPDATE jobs SET status = 'running' WHERE job_id = %s", (job_id,))
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to mark job {job_id} running: {error}") from error

    def record_job_result(self, job_id: int, exit_code: int, error_message: str | None, next_run_at: datetime) -> None:
        """Records one job invocation's outcome and reschedules it -- always flips status back to
        'idle' regardless of exit_code, since a failed run is still a finished run (next_run_at,
        computed by dispatch.algorithm.compute_next_run_at, is when it's retried)."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'idle', last_run_at = CURRENT_TIMESTAMP, last_exit_code = %s, last_error = %s, next_run_at = %s
                    WHERE job_id = %s
                    """,
                    (exit_code, error_message, next_run_at, job_id),
                )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to record result for job {job_id}: {error}") from error
