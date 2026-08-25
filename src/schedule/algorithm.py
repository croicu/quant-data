"""Pure work-item decomposition logic -- no database access, so it's directly unit-testable.

quant-schedule (croicu/quant-data#68) takes a bulk backfill request (a ticker + date range) and
breaks it into a job graph for quant_schedule.jobs: one ingest job per (day, provider) -- or per
(day, method) for ibkr, since it's the only provider with more than one method -- followed by one
staging job depending on every ingest job, followed by one reconcile job depending on the staging
job. quant-reconcile itself takes no ticker/date arguments (it processes the whole pending/staging
backlog), so a work item's reconcile job is always the same bare command.

Every calendar day in the range gets an ingest job, weekends included -- quant-ingest already
handles a weekend date correctly on its own (checks the calendar before fetching, marking it
covered without data for yfinance/massive rather than wasting an API call, per croicu/quant-data#56's
follow-up fix). An earlier version of this module filtered weekends out of the plan entirely,
which meant quant-ingest was never invoked for those dates at all -- so its own weekend handling
never ran, and archive_coverage ended up with a real gap (three disjoint ranges instead of one
continuous one) instead of a weekend marked covered-without-data. Confirmed live: a QQQ
2026-08-03..08-18 backfill produced archive_coverage rows split at both weekends.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from quant_data._internal.shared.schedule_writer import NewJob

_IBKR_PROVIDER_NAME = "ibkr"


def _calendar_days(start_date: date, end_date: date) -> list[date]:
    days: list[date] = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def build_job_plan(
    ticker: str,
    start_date: date,
    end_date: date,
    providers: list[str],
    ibkr_methods: list[str],
    now: datetime,
    retry_interval_seconds: int,
) -> list[NewJob]:
    """Returns the full job graph for one work item, in dependency order (every ingest job, then
    the staging job, then the reconcile job) -- the order WorkItemScheduleWriter.create_jobs
    requires, since it resolves depends_on_names against job IDs created earlier in the same
    batch."""
    calendar_days = _calendar_days(start_date, end_date)
    date_range_label = f"{start_date.isoformat()}-{end_date.isoformat()}"

    ingest_jobs: list[NewJob] = []
    for calendar_day in calendar_days:
        day_label = calendar_day.isoformat()
        for provider in providers:
            if provider == _IBKR_PROVIDER_NAME:
                for method in ibkr_methods:
                    ingest_jobs.append(
                        NewJob(
                            name=f"workitem-{ticker}-ingest-{day_label}-{provider}-{method}",
                            command=[
                                "quant-ingest",
                                "--ticker",
                                ticker,
                                "--start-date",
                                day_label,
                                "--end-date",
                                day_label,
                                "--providers",
                                provider,
                                "--ibkr-methods",
                                method,
                            ],
                            interval_seconds=retry_interval_seconds,
                            next_run_at=now,
                            run_once=True,
                        )
                    )
            else:
                ingest_jobs.append(
                    NewJob(
                        name=f"workitem-{ticker}-ingest-{day_label}-{provider}",
                        command=[
                            "quant-ingest",
                            "--ticker",
                            ticker,
                            "--start-date",
                            day_label,
                            "--end-date",
                            day_label,
                            "--providers",
                            provider,
                        ],
                        interval_seconds=retry_interval_seconds,
                        next_run_at=now,
                        run_once=True,
                    )
                )

    stage_job = NewJob(
        name=f"workitem-{ticker}-stage-{date_range_label}",
        command=[
            "quant-stage",
            "--ticker",
            ticker,
            "--start-date",
            start_date.isoformat(),
            "--end-date",
            end_date.isoformat(),
            "--providers",
            ",".join(providers),
        ],
        interval_seconds=retry_interval_seconds,
        next_run_at=now,
        run_once=True,
        depends_on_names=[job.name for job in ingest_jobs],
    )

    reconcile_job = NewJob(
        name=f"workitem-{ticker}-reconcile-{date_range_label}",
        command=["quant-reconcile"],
        interval_seconds=retry_interval_seconds,
        next_run_at=now,
        run_once=True,
        depends_on_names=[stage_job.name],
    )

    return ingest_jobs + [stage_job, reconcile_job]
