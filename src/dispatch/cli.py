from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quant_data._internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule import JobRow, ScheduleDatabase
from quant_data._internal.shared.settings import PostgresSettings, Settings
from quant_data._internal.shared.transports import resolve_transport

from .algorithm import compute_next_run_at

CATEGORY_DISPATCH = "dispatch"

# Truncates an overlong subprocess stderr before it's stored in jobs.last_error -- a runaway
# traceback shouldn't grow that column unboundedly.
MAX_ERROR_MESSAGE_LENGTH = 2000


@dataclass
class CliArguments:
    debug: bool = False


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-dispatch",
        usage="quant-dispatch [--debug]",
        fromfile_prefix_chars="@",
        description=(
            "Checks the jobs table for anything due, runs each due job as a subprocess (e.g. "
            "quant-ingest --catch-up), and records the result -- then exits. One-shot, not a "
            "daemon: something host-specific (a cron entry or systemd timer, set up outside this "
            "repo) is responsible for invoking quant-dispatch repeatedly. See "
            "croicu/quant-data#66."
        ),
    )
    parser.add_argument("--debug", action="store_true", default=False, help="override settings.json's debug flag")
    args = parser.parse_args(argv)
    return CliArguments(debug=args.debug)


def _default_database_factory(postgres_settings: PostgresSettings) -> ScheduleDatabase:
    if postgres_settings.worker is None:
        raise AppError("settings.postgres.worker is required to run quant-dispatch -- there is nowhere to read jobs from without it.")
    transport = resolve_transport(
        host=postgres_settings.host,
        port=postgres_settings.port,
        ssh_user=postgres_settings.ssh_user,
        ssh_key_path=postgres_settings.ssh_key_path,
    )
    return ScheduleDatabase(
        transport=transport,
        user=postgres_settings.worker.user,
        password=postgres_settings.worker.password,
        dbname=postgres_settings.worker.dbname,
    )


def _resolve_executable(name: str, bin_dir: Path) -> str:
    """Resolves `name` against `bin_dir` (quant-dispatch's own venv/bin directory, sys.executable's
    parent) if a file by that name lives there, falling back to the bare name otherwise -- letting
    subprocess.run's normal PATH lookup handle a command that genuinely isn't one of this venv's
    own console scripts. quant-ingest/quant-stage/quant-reconcile are installed as siblings of
    quant-dispatch itself (same `pip install -e` into the same venv, all four are `[project.
    scripts]` entries), but subprocess.run's default PATH-based lookup uses the *inherited*
    environment, not quant-dispatch's own venv -- unattended via cron/systemd, PATH is often a
    minimal system default that was never told about this venv's bin directory, which would
    otherwise fail the very first time a job actually runs unattended."""
    sibling_path = bin_dir / name
    if sibling_path.exists():
        return str(sibling_path)
    return name


def _default_subprocess_runner(command: list[str]) -> subprocess.CompletedProcess:
    # No explicit cwd -- inherits quant-dispatch's own working directory. That's already required
    # to be correct for quant-dispatch's own Settings.load() to find settings.json (its default
    # path is relative, "./settings.json"), so whatever cron/systemd entry invokes quant-dispatch
    # must already set cwd to the repo root for quant-dispatch to start at all -- child jobs get
    # that same correct cwd for free, no separate assumption needed here.
    bin_dir = Path(sys.executable).parent
    resolved_command = [_resolve_executable(command[0], bin_dir)] + command[1:]
    return subprocess.run(resolved_command, capture_output=True, text=True)


def _run_job(
    job: JobRow,
    database: ScheduleDatabase,
    now: datetime,
    subprocess_runner: Callable[[list[str]], subprocess.CompletedProcess],
) -> bool:
    Logger.info(f"quant-dispatch: starting job '{job.name}': {' '.join(job.command)}", category=CATEGORY_DISPATCH)
    database.mark_job_running(job.job_id)

    try:
        result = subprocess_runner(job.command)
        exit_code = result.returncode
        error_message = None if exit_code == 0 else (result.stderr or "").strip()[:MAX_ERROR_MESSAGE_LENGTH] or None
    except OSError as error:
        # e.g. the command's own executable isn't on PATH -- a launch failure, not a job failure;
        # still recorded against the job rather than aborting the whole dispatch run.
        exit_code = -1
        error_message = str(error)

    next_run_at = compute_next_run_at(now, job.interval_seconds)

    if exit_code == 0:
        # A run_once job (croicu/quant-data#68) is disabled instead of rescheduled once it
        # actually succeeds -- a failure still reschedules/retries normally either way.
        database.record_job_result(job.job_id, exit_code, error_message, next_run_at, disable=job.run_once)
        if job.run_once:
            Logger.info(f"quant-dispatch: job '{job.name}' succeeded (run_once), disabled.", category=CATEGORY_DISPATCH)
        else:
            Logger.info(f"quant-dispatch: job '{job.name}' succeeded, next run at {next_run_at.isoformat()}.", category=CATEGORY_DISPATCH)
        return True

    database.record_job_result(job.job_id, exit_code, error_message, next_run_at)

    Logger.warning(
        f"quant-dispatch: job '{job.name}' failed (exit code {exit_code}): {error_message}",
        category=CATEGORY_DISPATCH,
    )
    return False


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    database_factory: Callable[[PostgresSettings], ScheduleDatabase] | None = None,
    subprocess_runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = Settings.load() if settings_path is None else Settings.load(path=settings_path)
    except AppError as error:
        print(f"quant-dispatch: error: {error}", file=sys.stderr)
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
        Logger.info("quant-dispatch: started.")

        if settings.postgres is None:
            raise AppError("settings.postgres is required to run quant-dispatch.")

        active_database_factory = database_factory if database_factory is not None else _default_database_factory
        active_subprocess_runner = subprocess_runner if subprocess_runner is not None else _default_subprocess_runner
        active_now = (lambda: datetime.now(timezone.utc)) if now is None else now

        succeeded_count = 0
        failed_count = 0

        database = active_database_factory(settings.postgres)
        try:
            current_time = active_now()
            due_jobs = database.fetch_due_jobs(current_time)

            if not due_jobs:
                Logger.info("quant-dispatch: no jobs due.", category=CATEGORY_DISPATCH)
            else:
                # Sequential and blocking on purpose, not just the simplest loop shape: if the
                # dispatcher's own trigger was down for a while (e.g. the repo owner on vacation)
                # and several jobs are due at once on resume, they run one at a time instead of
                # all launching concurrently -- avoids a "job storm" against the same database.
                for job in due_jobs:
                    if _run_job(job, database, current_time, active_subprocess_runner):
                        succeeded_count += 1
                    else:
                        failed_count += 1
        finally:
            database.close()

        Logger.info(f"quant-dispatch: completed ({succeeded_count} succeeded, {failed_count} failed).")
        return 1 if failed_count else 0
    except AppError as error:
        if debug:
            raise
        print(f"quant-dispatch: error: {error}", file=sys.stderr)
        return 1
    finally:
        Logger.set_logger(None)


if __name__ == "__main__":
    raise SystemExit(main())
