from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from quant_data._internal.contracts import ConnectionTransport
from quant_data.protocols import LoggingSink

from .diagnostics import Logger
from .errors import AppError

CATEGORY_SCHEDULE = "schedule"


@dataclass
class NewJob:
    name: str
    command: list[str]
    interval_seconds: int
    next_run_at: datetime
    run_once: bool
    depends_on_names: list[str] = field(default_factory=list)


class WorkItemScheduleWriter:
    """Write-only client for quant_schedule (croicu/quant-data#68) -- creates a work item's whole
    job graph (ingest jobs, a staging job depending on them, a reconcile job depending on that).
    Deliberately a separate class from ScheduleDatabase, not a method on it: ScheduleDatabase's own
    docstring records that it connects as quant_worker and never issues an INSERT -- job creation
    is a different actor (quant_scheduler, full CRUD on jobs/job_dependencies) doing a different
    thing, same precedent as ProviderSourceArchiveWriter/Reader being split from PostgresDatabase.
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

    def create_jobs(self, jobs: list[NewJob]) -> dict[str, int]:
        """Inserts a whole batch of jobs (and their job_dependencies rows) in one transaction --
        either the entire work item's job graph is created, or none of it is. `jobs` must list
        each job after everything it depends on (build_job_plan already returns them in that
        order): depends_on_names is resolved against job IDs created earlier in this same call,
        not against any job that may already exist in the database."""
        name_to_id: dict[str, int] = {}
        try:
            with self._connection.cursor() as cursor:
                for job in jobs:
                    cursor.execute(
                        """
                        INSERT INTO jobs (name, command, interval_seconds, next_run_at, run_once)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING job_id
                        """,
                        (job.name, job.command, job.interval_seconds, job.next_run_at, job.run_once),
                    )
                    job_id = cursor.fetchone()[0]
                    name_to_id[job.name] = job_id

                    for dependency_name in job.depends_on_names:
                        if dependency_name not in name_to_id:
                            raise AppError(
                                f"Job '{job.name}' depends on '{dependency_name}', which wasn't created earlier in "
                                "this same batch -- dependencies must be listed before their dependents."
                            )
                        cursor.execute(
                            "INSERT INTO job_dependencies (job_id, depends_on_job_id) VALUES (%s, %s)",
                            (job_id, name_to_id[dependency_name]),
                        )
            self._connection.commit()
        except psycopg.errors.UniqueViolation as error:
            self._connection.rollback()
            raise AppError(f"Failed to create work-item jobs: a job with one of these names already exists ({error}).") from error
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to create work-item jobs: {error}") from error
        except AppError:
            self._connection.rollback()
            raise

        return name_to_id
