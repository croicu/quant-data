from __future__ import annotations

from datetime import datetime

from quant_data._internal.shared.schedule import JobRow


class FakeDispatchDatabase:
    """In-memory stand-in for ScheduleDatabase (croicu/quant-data#66)."""

    def __init__(self, jobs: list[JobRow] | None = None) -> None:
        self.jobs = jobs if jobs is not None else []
        self.running_job_ids: list[int] = []
        self.recorded_results: list[tuple[int, int, str | None, datetime]] = []
        self.closed = False

    def fetch_due_jobs(self, now: datetime) -> list[JobRow]:
        return list(self.jobs)

    def mark_job_running(self, job_id: int) -> None:
        self.running_job_ids.append(job_id)

    def record_job_result(self, job_id: int, exit_code: int, error_message: str | None, next_run_at: datetime) -> None:
        self.recorded_results.append((job_id, exit_code, error_message, next_run_at))

    def close(self) -> None:
        self.closed = True
