from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from pathlib import Path

from quant_data._internal.contracts import DEFAULT_METHODS_BY_PROVIDER
from quant_data._internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule_writer import NewJob, WorkItemScheduleWriter
from quant_data._internal.shared.settings import PostgresSettings, Settings
from quant_data._internal.shared.transports import resolve_transport

from .algorithm import build_job_plan

CATEGORY_WORKITEM = "workitem"

DEFAULT_RETRY_INTERVAL_SECONDS = 300


@dataclass
class CliArguments:
    ticker: str = ""
    start_date: date_type = date_type.min
    end_date: date_type | None = None
    providers: list[str] | None = None
    ibkr_methods: list[str] | None = None
    retry_interval_seconds: int = DEFAULT_RETRY_INTERVAL_SECONDS
    dry_run: bool = False
    debug: bool = False


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-schedule",
        usage=(
            "quant-schedule --ticker TICKER --start-date YYYY-MM-DD [--end-date YYYY-MM-DD] "
            "[--providers NAME,...] [--ibkr-methods METHOD,...] [--retry-interval-seconds N] [--dry-run] [--debug]"
        ),
        fromfile_prefix_chars="@",
        description=(
            "Decomposes a bulk backfill request (one ticker, an inclusive date range) into a job "
            "graph in quant_schedule.jobs: one ingest job per (day, provider) -- or per "
            "(day, method) for ibkr, its only multi-method provider -- followed by one staging job "
            "depending on every ingest job, followed by one reconcile job depending on the staging "
            "job. quant-dispatch (croicu/quant-data#66) then runs each job as it becomes due -- "
            "this process only creates the job rows, it does not run anything itself. Every "
            "created job is run_once: it's disabled after it succeeds, and simply retries on its "
            "own interval if it fails. --dry-run prints the planned jobs without touching the "
            "database."
        ),
    )

    parser.add_argument("--ticker", required=True, help="single ticker to back-fill, e.g. QQQ")
    parser.add_argument("--start-date", required=True, help="first trading date to back-fill, YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="last trading date to back-fill (inclusive), YYYY-MM-DD; omit to default to --start-date")
    parser.add_argument(
        "--providers",
        default=None,
        help="comma-separated provider names to use instead of settings.providers, e.g. yfinance,ibkr",
    )
    parser.add_argument(
        "--ibkr-methods",
        default=None,
        help="comma-separated IBKR methods to use instead of settings.ibkr.methods, e.g. TRADES,BID_ASK,MIDPOINT (case as given, not normalized)",
    )
    parser.add_argument(
        "--retry-interval-seconds",
        type=int,
        default=DEFAULT_RETRY_INTERVAL_SECONDS,
        help=f"how long a failed job waits before quant-dispatch retries it (default {DEFAULT_RETRY_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="print the planned jobs without writing them to quant_schedule",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="override settings.json's debug flag",
    )

    args = parser.parse_args(argv)

    ticker = args.ticker.strip().upper()
    if not ticker:
        parser.error("--ticker must not be empty.")

    try:
        start_date = date_type.fromisoformat(args.start_date)
    except ValueError as error:
        parser.error(f"--start-date must be YYYY-MM-DD: {error}")

    end_date = start_date
    if args.end_date is not None:
        try:
            end_date = date_type.fromisoformat(args.end_date)
        except ValueError as error:
            parser.error(f"--end-date must be YYYY-MM-DD: {error}")

    if end_date < start_date:
        parser.error(f"--end-date ({end_date.isoformat()}) must not be before --start-date ({start_date.isoformat()}).")

    if args.retry_interval_seconds < 1:
        parser.error(f"--retry-interval-seconds must be at least 1, got {args.retry_interval_seconds}.")

    providers: list[str] | None = None
    if args.providers is not None:
        providers = []
        for name in args.providers.split(","):
            normalized = name.strip().lower()
            if not normalized:
                parser.error("--providers must be a comma-separated list of non-empty provider names.")
            providers.append(normalized)

    ibkr_methods: list[str] | None = None
    if args.ibkr_methods is not None:
        ibkr_methods = []
        for method_name in args.ibkr_methods.split(","):
            stripped = method_name.strip()
            if not stripped:
                parser.error("--ibkr-methods must be a comma-separated list of non-empty method names.")
            ibkr_methods.append(stripped)

    return CliArguments(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        providers=providers,
        ibkr_methods=ibkr_methods,
        retry_interval_seconds=args.retry_interval_seconds,
        dry_run=args.dry_run,
        debug=args.debug,
    )


def _default_writer_factory(postgres_settings: PostgresSettings) -> WorkItemScheduleWriter:
    if postgres_settings.scheduler is None:
        raise AppError("settings.postgres.scheduler is required to run quant-schedule -- there is nowhere to write jobs to without it.")
    transport = resolve_transport(
        host=postgres_settings.host,
        port=postgres_settings.port,
        ssh_user=postgres_settings.ssh_user,
        ssh_key_path=postgres_settings.ssh_key_path,
    )
    return WorkItemScheduleWriter(
        transport=transport,
        user=postgres_settings.scheduler.user,
        password=postgres_settings.scheduler.password,
        dbname=postgres_settings.scheduler.dbname,
    )


def _print_plan(jobs: list[NewJob]) -> None:
    print(f"quant-schedule: --dry-run, {len(jobs)} job(s) planned (nothing written):")
    for job in jobs:
        depends_on = ", ".join(job.depends_on_names) if job.depends_on_names else "(none)"
        print(f"  - {job.name}")
        print(f"      command: {' '.join(job.command)}")
        print(f"      run_once={job.run_once} interval_seconds={job.interval_seconds} depends_on: {depends_on}")


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    writer_factory: Callable[[PostgresSettings], WorkItemScheduleWriter] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = Settings.load() if settings_path is None else Settings.load(path=settings_path)
    except AppError as error:
        print(f"quant-schedule: error: {error}", file=sys.stderr)
        return 1

    debug = settings.debug or arguments.debug

    Logger.set_logger(
        ConsoleLogSink(
            min_level=settings.logging,
            categories=settings.log_categories,
            excluded_categories=settings.excluded_categories,
        )
    )
    try:
        Logger.info("quant-schedule: started.", category=CATEGORY_WORKITEM)

        if settings.postgres is None:
            raise AppError("settings.postgres is required to run quant-schedule.")

        providers = arguments.providers if arguments.providers is not None else settings.providers
        if not providers:
            raise AppError("No provider configured -- pass --providers or configure settings.providers.")

        ibkr_methods = arguments.ibkr_methods
        if ibkr_methods is None:
            ibkr_methods = settings.ibkr.methods if settings.ibkr.methods is not None else DEFAULT_METHODS_BY_PROVIDER["ibkr"]

        active_now = (lambda: datetime.now(timezone.utc)) if now is None else now

        plan = build_job_plan(
            ticker=arguments.ticker,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            providers=providers,
            ibkr_methods=ibkr_methods,
            now=active_now(),
            retry_interval_seconds=arguments.retry_interval_seconds,
        )

        if arguments.dry_run:
            _print_plan(plan)
            Logger.info(f"quant-schedule: dry run complete, {len(plan)} job(s) planned.", category=CATEGORY_WORKITEM)
            return 0

        active_writer_factory = writer_factory if writer_factory is not None else _default_writer_factory
        writer = active_writer_factory(settings.postgres)
        try:
            name_to_id = writer.create_jobs(plan)
        finally:
            writer.close()

        Logger.info(
            f"quant-schedule: created {len(name_to_id)} job(s) for {arguments.ticker} {arguments.start_date.isoformat()}-{arguments.end_date.isoformat()}.",
            category=CATEGORY_WORKITEM,
        )
        return 0
    except AppError as error:
        if debug:
            raise
        print(f"quant-schedule: error: {error}", file=sys.stderr)
        return 1
    finally:
        Logger.set_logger(None)


if __name__ == "__main__":
    raise SystemExit(main())
