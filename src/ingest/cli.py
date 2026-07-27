from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from quant_data_internal.contracts import IntraDayProvider
from quant_data_internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data_internal.shared.errors import AppError
from quant_data_internal.shared.postgres import PostgresDatabase
from quant_data_internal.shared.providers.yf import YahooFinanceIntraDay
from quant_data_internal.shared.settings import PostgresSettings, Settings

CATEGORY_INGEST = "ingest"


@dataclass
class CliArguments:
    ticker: str | None = None
    start_date: date_type | None = None
    end_date: date_type | None = None
    debug: bool = False


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-ingest",
        usage="quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD]] [--ticker TICKER] [--debug]",
        description=(
            "Fetch 1-minute OHLCV bars for one ticker (with --ticker) or every ticker in "
            "settings.tickers (without --ticker), over an inclusive date range (--start-date alone "
            "means a single day; add --end-date for a range; omit both to use "
            "settings.startDate/settings.endDate), and store them in quant-data's warehouse."
        ),
    )

    parser.add_argument("--ticker", default=None, help="single ticker to fetch, e.g. AAPL; omit to use settings.tickers")
    parser.add_argument("--start-date", default=None, help="first trading date to fetch, YYYY-MM-DD; omit to use settings.startDate")
    parser.add_argument("--end-date", default=None, help="last trading date to fetch (inclusive), YYYY-MM-DD; omit to default to --start-date (a single day)")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="override settings.json's debug flag",
    )

    args = parser.parse_args(argv)

    if args.end_date is not None and args.start_date is None:
        parser.error("--end-date requires --start-date.")

    start_date: date_type | None = None
    end_date: date_type | None = None
    if args.start_date is not None:
        try:
            start_date = date_type.fromisoformat(args.start_date)
        except ValueError as error:
            parser.error(f"--start-date must be YYYY-MM-DD: {error}")

        if args.end_date is not None:
            try:
                end_date = date_type.fromisoformat(args.end_date)
            except ValueError as error:
                parser.error(f"--end-date must be YYYY-MM-DD: {error}")
        else:
            end_date = start_date

        if end_date < start_date:
            parser.error(f"--end-date ({end_date.isoformat()}) must not be before --start-date ({start_date.isoformat()}).")

    return CliArguments(ticker=args.ticker, start_date=start_date, end_date=end_date, debug=args.debug)


def _date_range(start_date: date_type, end_date: date_type) -> list[date_type]:
    dates: list[date_type] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _default_database_factory(postgres_settings: PostgresSettings) -> PostgresDatabase:
    return PostgresDatabase(
        host=postgres_settings.host,
        port=postgres_settings.port,
        user=postgres_settings.user,
        password=postgres_settings.password,
        dbname=postgres_settings.dbname,
    )


def _ingest_one(provider: IntraDayProvider, database: PostgresDatabase, ticker: str, target_date: date_type) -> int:
    bars = provider.fetch_bars(ticker, target_date)
    written = database.write_bars(bars)

    incomplete_count = 0
    for bar in bars:
        if bar.incomplete:
            incomplete_count += 1

    Logger.info(
        f"quant-ingest: wrote {written} bars for {ticker.upper()} on {target_date.isoformat()} ({incomplete_count} incomplete).",
        category=CATEGORY_INGEST,
    )
    return written


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    provider: IntraDayProvider | None = None,
    database_factory: Callable[[PostgresSettings], PostgresDatabase] | None = None,
) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = Settings.load() if settings_path is None else Settings.load(path=settings_path)
    except AppError as error:
        print(f"quant-ingest: error: {error}", file=sys.stderr)
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
        Logger.info("quant-ingest: started.")

        if settings.postgres is None:
            raise AppError("settings.postgres is required to run quant-ingest.")

        tickers = [arguments.ticker] if arguments.ticker is not None else settings.tickers
        if not tickers:
            raise AppError("No ticker given and settings.tickers is empty — pass --ticker or configure settings.tickers.")

        if arguments.start_date is not None and arguments.end_date is not None:
            effective_start_date = arguments.start_date
            effective_end_date = arguments.end_date
        elif settings.start_date is not None and settings.end_date is not None:
            effective_start_date = settings.start_date
            effective_end_date = settings.end_date
        else:
            raise AppError(
                "No date range given and settings.startDate/settings.endDate not configured — pass --start-date/--end-date or configure both in settings."
            )

        target_dates = _date_range(effective_start_date, effective_end_date)

        active_provider = provider if provider is not None else YahooFinanceIntraDay()
        active_database_factory = database_factory if database_factory is not None else _default_database_factory

        database = active_database_factory(settings.postgres)
        succeeded: list[tuple[str, date_type]] = []
        failed: list[tuple[str, date_type]] = []
        try:
            for target_date in target_dates:
                for ticker in tickers:
                    try:
                        _ingest_one(active_provider, database, ticker, target_date)
                        succeeded.append((ticker, target_date))
                    except AppError as error:
                        Logger.warning(
                            f"quant-ingest: failed to ingest '{ticker.upper()}' on {target_date.isoformat()}: {error}",
                            category=CATEGORY_INGEST,
                        )
                        failed.append((ticker, target_date))
        finally:
            database.close()

        Logger.info(f"quant-ingest: completed ({len(succeeded)} succeeded, {len(failed)} failed).")
        return 1 if failed else 0
    except AppError as error:
        if debug:
            raise
        print(f"quant-ingest: error: {error}", file=sys.stderr)
        return 1
    finally:
        Logger.set_logger(None)


if __name__ == "__main__":
    raise SystemExit(main())
