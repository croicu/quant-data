from __future__ import annotations

from datetime import datetime, timezone

from dispatch.algorithm import compute_next_run_at


def test_compute_next_run_at_adds_interval_to_now():
    now = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)

    next_run_at = compute_next_run_at(now, interval_seconds=3600)

    assert next_run_at == datetime(2026, 1, 2, 13, 0, 0, tzinfo=timezone.utc)


def test_compute_next_run_at_ignores_any_prior_schedule():
    # Deliberately schedules from `now`, not from a job's own prior next_run_at -- a dispatcher
    # that was down for a while shouldn't pile up a burst of immediately-due catch-up runs.
    now = datetime(2026, 1, 2, 18, 45, 0, tzinfo=timezone.utc)

    next_run_at = compute_next_run_at(now, interval_seconds=900)

    assert next_run_at == datetime(2026, 1, 2, 19, 0, 0, tzinfo=timezone.utc)
