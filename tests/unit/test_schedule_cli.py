from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.schedule_writer import NewJob
from quant_data._internal.shared.settings import Settings
from schedule import cli

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


class _FakeWriter:
    def __init__(self) -> None:
        self.created_batches: list[list[NewJob]] = []
        self.closed = False

    def create_jobs(self, jobs: list[NewJob]) -> dict[str, int]:
        self.created_batches.append(jobs)
        return {job.name: index for index, job in enumerate(jobs)}

    def close(self) -> None:
        self.closed = True


def _use_writer(writer: _FakeWriter):
    def factory(postgres_settings):
        return writer

    return factory


def test_parse_args_requires_ticker_and_start_date():
    with pytest.raises(SystemExit):
        cli.parse_args([])


def test_parse_args_defaults_end_date_to_start_date():
    arguments = cli.parse_args(["--ticker", "qqq", "--start-date", "2026-08-03"])

    assert arguments.ticker == "QQQ"
    assert arguments.start_date == date(2026, 8, 3)
    assert arguments.end_date == date(2026, 8, 3)


def test_parse_args_rejects_end_date_before_start_date():
    with pytest.raises(SystemExit):
        cli.parse_args(["--ticker", "QQQ", "--start-date", "2026-08-10", "--end-date", "2026-08-03"])


def test_parse_args_parses_providers_and_ibkr_methods():
    arguments = cli.parse_args(
        [
            "--ticker",
            "QQQ",
            "--start-date",
            "2026-08-03",
            "--providers",
            "yfinance,ibkr",
            "--ibkr-methods",
            "TRADES,BID_ASK",
        ]
    )

    assert arguments.providers == ["yfinance", "ibkr"]
    assert arguments.ibkr_methods == ["TRADES", "BID_ASK"]


def test_main_dry_run_does_not_touch_writer(capsys):
    exit_code = cli.main(
        ["--ticker", "QQQ", "--start-date", "2026-08-03", "--dry-run"],
        settings_path=SETTINGS_PATH,
        writer_factory=lambda postgres_settings: (_ for _ in ()).throw(AssertionError("writer_factory should not be called on --dry-run")),
        now=lambda: _NOW,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    assert "workitem-QQQ-ingest-2026-08-03-yfinance" in captured.out


def test_main_creates_jobs_via_writer_and_closes_it():
    writer = _FakeWriter()

    exit_code = cli.main(
        ["--ticker", "QQQ", "--start-date", "2026-08-03"],
        settings_path=SETTINGS_PATH,
        writer_factory=_use_writer(writer),
        now=lambda: _NOW,
    )

    assert exit_code == 0
    assert len(writer.created_batches) == 1
    assert writer.closed is True
    job_names = {job.name for job in writer.created_batches[0]}
    assert "workitem-QQQ-ingest-2026-08-03-yfinance" in job_names


def test_main_uses_settings_providers_when_not_passed():
    writer = _FakeWriter()

    cli.main(
        ["--ticker", "QQQ", "--start-date", "2026-08-03"],
        settings_path=SETTINGS_PATH,
        writer_factory=_use_writer(writer),
        now=lambda: _NOW,
    )

    # tests/data/settings.json configures providers: ["yfinance"] and no ibkr, so only one
    # ingest job (yfinance, no method suffix) plus stage and reconcile should be created.
    job_names = [job.name for job in writer.created_batches[0]]
    assert job_names == [
        "workitem-QQQ-ingest-2026-08-03-yfinance",
        "workitem-QQQ-stage-2026-08-03-2026-08-03",
        "workitem-QQQ-reconcile-2026-08-03-2026-08-03",
    ]


def test_default_writer_factory_raises_when_scheduler_not_configured():
    settings = Settings.load(path=SETTINGS_PATH)

    with pytest.raises(AppError):
        cli._default_writer_factory(settings.postgres)
