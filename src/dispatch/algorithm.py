"""Pure job-scheduling logic -- no database access, so it's directly unit-testable.

quant-dispatch (croicu/quant-data#66) is a one-shot CLI: check the jobs table for anything due,
run each due job as a subprocess, record the result. The only genuinely algorithmic piece is
computing a job's next run time.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def compute_next_run_at(now: datetime, interval_seconds: int) -> datetime:
    """Schedules the next run interval_seconds after `now` -- the moment this dispatch actually
    ran a job, not the job's own prior next_run_at. Deliberate: scheduling from the prior
    next_run_at would let a dispatcher that was down or delayed pile up a burst of immediately-due
    catch-up runs the moment it resumes; scheduling from `now` instead just picks up the interval
    from wherever the dispatcher actually caught up. Matches the interval-based (not
    cron-expression) scheduling model from croicu/quant-data#66's converged design."""
    return now + timedelta(seconds=interval_seconds)
