from __future__ import annotations

from datetime import date, datetime, timezone

from schedule.algorithm import build_job_plan

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_build_job_plan_includes_weekends():
    # 2026-08-08 is a Saturday, 2026-08-09 a Sunday -- included on purpose (croicu/quant-data#68
    # follow-up): quant-ingest already handles a weekend date correctly on its own (marks it
    # covered without data rather than wasting an API call), and skipping it here entirely used to
    # leave a real gap in archive_coverage instead of a continuous range.
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 10),
        providers=["yfinance"],
        ibkr_methods=[],
        now=_NOW,
        retry_interval_seconds=300,
    )

    ingest_names = [job.name for job in plan if "ingest" in job.name]
    assert ingest_names == [
        "workitem-QQQ-ingest-2026-08-07-yfinance",
        "workitem-QQQ-ingest-2026-08-08-yfinance",
        "workitem-QQQ-ingest-2026-08-09-yfinance",
        "workitem-QQQ-ingest-2026-08-10-yfinance",
    ]


def test_build_job_plan_one_job_per_ibkr_method_only_for_ibkr():
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
        providers=["yfinance", "ibkr"],
        ibkr_methods=["TRADES", "BID_ASK", "MIDPOINT"],
        now=_NOW,
        retry_interval_seconds=300,
    )

    ingest_names = sorted(job.name for job in plan if "ingest" in job.name)
    assert ingest_names == sorted(
        [
            "workitem-QQQ-ingest-2026-08-03-yfinance",
            "workitem-QQQ-ingest-2026-08-03-ibkr-TRADES",
            "workitem-QQQ-ingest-2026-08-03-ibkr-BID_ASK",
            "workitem-QQQ-ingest-2026-08-03-ibkr-MIDPOINT",
        ]
    )

    ibkr_job = next(job for job in plan if job.name == "workitem-QQQ-ingest-2026-08-03-ibkr-TRADES")
    assert ibkr_job.command == [
        "quant-ingest",
        "--ticker",
        "QQQ",
        "--start-date",
        "2026-08-03",
        "--end-date",
        "2026-08-03",
        "--providers",
        "ibkr",
        "--ibkr-methods",
        "TRADES",
    ]

    yfinance_job = next(job for job in plan if job.name == "workitem-QQQ-ingest-2026-08-03-yfinance")
    assert "--ibkr-methods" not in yfinance_job.command


def test_build_job_plan_stage_depends_on_every_ingest_job():
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 4),
        providers=["yfinance", "massive"],
        ibkr_methods=[],
        now=_NOW,
        retry_interval_seconds=300,
    )

    ingest_names = {job.name for job in plan if "ingest" in job.name}
    stage_job = next(job for job in plan if "stage" in job.name)

    assert set(stage_job.depends_on_names) == ingest_names
    assert stage_job.command == [
        "quant-stage",
        "--ticker",
        "QQQ",
        "--start-date",
        "2026-08-03",
        "--end-date",
        "2026-08-04",
        "--providers",
        "yfinance,massive",
    ]


def test_build_job_plan_reconcile_depends_only_on_stage():
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
        providers=["yfinance"],
        ibkr_methods=[],
        now=_NOW,
        retry_interval_seconds=300,
    )

    stage_job = next(job for job in plan if "stage" in job.name)
    reconcile_job = next(job for job in plan if "reconcile" in job.name)

    assert reconcile_job.depends_on_names == [stage_job.name]
    assert reconcile_job.command == ["quant-reconcile"]


def test_build_job_plan_every_job_is_run_once_and_ordered_before_its_dependents():
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
        providers=["yfinance"],
        ibkr_methods=[],
        now=_NOW,
        retry_interval_seconds=300,
    )

    assert all(job.run_once for job in plan)

    seen: set[str] = set()
    for job in plan:
        assert set(job.depends_on_names).issubset(seen)
        seen.add(job.name)


def test_build_job_plan_uses_retry_interval_and_now_for_every_job():
    plan = build_job_plan(
        ticker="QQQ",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
        providers=["yfinance"],
        ibkr_methods=[],
        now=_NOW,
        retry_interval_seconds=600,
    )

    assert all(job.interval_seconds == 600 for job in plan)
    assert all(job.next_run_at == _NOW for job in plan)
