from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from quant_data._internal.contracts import DEFAULT_METHODS_BY_PROVIDER, PRIMARY_METHOD_BY_PROVIDER
from quant_data._internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.postgres import PostgresDatabase
from quant_data._internal.shared.provider_source_archive import ProviderSourceArchiveReader
from quant_data._internal.shared.settings import PostgresSettings, Settings
from quant_data._internal.shared.transports import resolve_transport
from quant_data.protocols import DataQuality

from .parsers import apply_supplementary_payload, parse_payload

CATEGORY_STAGE = "stage"


@dataclass
class CliArguments:
    ticker: str | None = None
    start_date: date_type | None = None
    end_date: date_type | None = None
    catch_up: bool = False
    debug: bool = False
    providers: list[str] | None = None


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-stage",
        usage="quant-stage [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up] [--ticker TICKER] [--providers NAME,...] [--debug]",
        fromfile_prefix_chars="@",
        description=(
            "Read the most recently archived provider_source_archive fetch for one ticker (with "
            "--ticker) or every ticker in settings.tickers (without --ticker), over an inclusive "
            "date range (--start-date alone means a single day; add --end-date for a range; omit "
            "both to use settings.startDate/settings.endDate), parse it, and write to "
            "quant_data's staging_market_data_1min -- the second half of quant-ingest's former "
            "single process (croicu/quant-data#56), reading what `quant-ingest` already archived "
            "to quant_ingest rather than fetching from providers itself. --catch-up re-processes "
            "the trailing settings.catchUpLookbackDays days instead, mirroring quant-ingest's own "
            "--catch-up. Arguments can also be read from a response file: `quant-stage "
            "@some-file.args`, one argument per line."
        ),
    )

    parser.add_argument("--ticker", default=None, help="single ticker to stage, e.g. AAPL; omit to use settings.tickers")
    parser.add_argument("--start-date", default=None, help="first trading date to stage, YYYY-MM-DD; omit to use settings.startDate")
    parser.add_argument("--end-date", default=None, help="last trading date to stage (inclusive), YYYY-MM-DD; omit to default to --start-date (a single day)")
    parser.add_argument(
        "--catch-up",
        action="store_true",
        default=False,
        help="re-process the trailing settings.catchUpLookbackDays days (excluding today) instead of a date range",
    )
    parser.add_argument(
        "--providers",
        default=None,
        help="comma-separated provider names to use instead of settings.providers, e.g. yfinance,massive",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="override settings.json's debug flag",
    )

    args = parser.parse_args(argv)

    if args.end_date is not None and args.start_date is None:
        parser.error("--end-date requires --start-date.")

    if args.catch_up and (args.start_date is not None or args.end_date is not None):
        parser.error("--catch-up cannot be combined with --start-date/--end-date.")

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

    providers: list[str] | None = None
    if args.providers is not None:
        providers = []
        for name in args.providers.split(","):
            normalized = name.strip().lower()
            if not normalized:
                parser.error("--providers must be a comma-separated list of non-empty provider names.")
            providers.append(normalized)

    return CliArguments(ticker=args.ticker, start_date=start_date, end_date=end_date, catch_up=args.catch_up, debug=args.debug, providers=providers)


def _date_range(start_date: date_type, end_date: date_type) -> list[date_type]:
    dates: list[date_type] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _is_weekend(target_date: date_type) -> bool:
    return target_date.weekday() >= 5


def _default_database_factory(postgres_settings: PostgresSettings) -> PostgresDatabase:
    transport = resolve_transport(
        host=postgres_settings.host,
        port=postgres_settings.port,
        ssh_user=postgres_settings.ssh_user,
        ssh_key_path=postgres_settings.ssh_key_path,
    )
    return PostgresDatabase(
        transport=transport,
        user=postgres_settings.user,
        password=postgres_settings.password,
        dbname=postgres_settings.dbname,
    )


def _default_archive_reader_factory(postgres_settings: PostgresSettings) -> ProviderSourceArchiveReader:
    if postgres_settings.archive_dbname is None:
        raise AppError("settings.postgres.archiveDbname is required to run quant-stage -- there is nothing to read from without it.")
    transport = resolve_transport(
        host=postgres_settings.host,
        port=postgres_settings.port,
        ssh_user=postgres_settings.ssh_user,
        ssh_key_path=postgres_settings.ssh_key_path,
    )
    return ProviderSourceArchiveReader(
        transport=transport,
        user=postgres_settings.user,
        password=postgres_settings.password,
        dbname=postgres_settings.archive_dbname,
    )


def _stage_one(
    providers: list[str],
    archive_reader: ProviderSourceArchiveReader,
    database: PostgresDatabase,
    ticker: str,
    target_date: date_type,
) -> int | None:
    # Mirrors ingest/cli.py's _ingest_one shape: each configured provider is staged independently,
    # blind to what other providers wrote for the same bar -- one provider having nothing archived
    # for this (ticker, date) doesn't stop the others from still being staged.
    Logger.diagnostic(
        f"quant-stage: starting {ticker.upper()} on {target_date.isoformat()}.",
        category=CATEGORY_STAGE,
    )

    total_written = 0
    total_incomplete = 0
    any_provider_staged = False

    for provider_name in providers:
        method = PRIMARY_METHOD_BY_PROVIDER.get(provider_name)
        if method is None:
            Logger.warning(
                f"quant-stage: no known archive method for provider '{provider_name}' -- skipping.",
                category=CATEGORY_STAGE,
            )
            continue

        try:
            archived = archive_reader.fetch_latest_bars(ticker, provider_name, method, target_date)
        except AppError as error:
            Logger.warning(
                f"quant-stage: failed to read archive for '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=CATEGORY_STAGE,
            )
            continue

        if archived is None:
            # Nothing archived for this (ticker, provider, date) -- not this process's job to go
            # fetch it; that's quant-ingest's responsibility.
            continue

        _payload_kind, payload = archived

        try:
            bars = parse_payload(provider_name, payload, ticker)
        except AppError as error:
            Logger.warning(
                f"quant-stage: failed to parse archived payload for '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=CATEGORY_STAGE,
            )
            continue

        # Supplement fields (croicu/quant-data#61) -- any method beyond this provider's primary
        # (e.g. IBKR's BID_ASK/MIDPOINT alongside TRADES) is archived separately but merges into
        # these same per-minute bars by timestamp, rather than becoming its own staging row.
        # Massive's wap/trade_count need no merge -- parse_payload above already pulled them
        # straight out of the primary aggregates payload.
        bars_by_timestamp = {}
        for bar in bars:
            bars_by_timestamp[bar.timestamp] = bar

        supplementary_methods = DEFAULT_METHODS_BY_PROVIDER.get(provider_name, [method])[1:]
        for supplementary_method in supplementary_methods:
            try:
                supplementary_archived = archive_reader.fetch_latest_bars(ticker, provider_name, supplementary_method, target_date)
            except AppError as error:
                Logger.warning(
                    f"quant-stage: failed to read '{supplementary_method}' archive for '{ticker.upper()}' on {target_date.isoformat()} "
                    f"via '{provider_name}': {error}",
                    category=CATEGORY_STAGE,
                )
                continue

            if supplementary_archived is None:
                continue

            _supplementary_payload_kind, supplementary_payload = supplementary_archived
            try:
                apply_supplementary_payload(provider_name, supplementary_method, supplementary_payload, bars_by_timestamp)
            except AppError as error:
                Logger.warning(
                    f"quant-stage: failed to parse '{supplementary_method}' archive for '{ticker.upper()}' on {target_date.isoformat()} "
                    f"via '{provider_name}': {error}",
                    category=CATEGORY_STAGE,
                )

        try:
            written = database.write_staging_bars(provider_name, bars)
        except AppError as error:
            Logger.warning(
                f"quant-stage: failed to write staging bars for '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=CATEGORY_STAGE,
            )
            continue

        try:
            database.record_ingestion_coverage(provider_name, ticker, target_date)
        except AppError as error:
            Logger.warning(
                f"quant-stage: failed to record ingestion coverage for '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=CATEGORY_STAGE,
            )

        any_provider_staged = True
        total_written += written
        for bar in bars:
            if bar.data_quality != DataQuality.ACCEPTED:
                total_incomplete += 1

    if not any_provider_staged:
        return None

    Logger.info(
        f"quant-stage: wrote {total_written} staging bars for {ticker.upper()} on {target_date.isoformat()} "
        f"across {len(providers)} provider(s) ({total_incomplete} incomplete).",
        category=CATEGORY_STAGE,
    )
    return total_written


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    database_factory: Callable[[PostgresSettings], PostgresDatabase] | None = None,
    archive_reader_factory: Callable[[PostgresSettings], ProviderSourceArchiveReader] | None = None,
    today: Callable[[], date_type] | None = None,
) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = Settings.load() if settings_path is None else Settings.load(path=settings_path)
    except AppError as error:
        print(f"quant-stage: error: {error}", file=sys.stderr)
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
        Logger.info("quant-stage: started.")

        if settings.postgres is None:
            raise AppError("settings.postgres is required to run quant-stage.")

        if arguments.providers is not None:
            settings.providers = arguments.providers

        if not settings.providers:
            raise AppError("No provider configured — pass --providers, a response file (e.g. @configs/all-providers.args), or configure settings.providers.")

        tickers = [arguments.ticker] if arguments.ticker is not None else settings.tickers
        if not tickers:
            raise AppError("No ticker given and settings.tickers is empty — pass --ticker or configure settings.tickers.")

        if arguments.catch_up:
            active_today = date_type.today() if today is None else today()
            effective_end_date = active_today - timedelta(days=1)
            effective_start_date = active_today - timedelta(days=settings.catch_up_lookback_days)
            Logger.info(
                f"quant-stage: catch-up mode, re-processing {effective_start_date.isoformat()} to {effective_end_date.isoformat()}.",
                category=CATEGORY_STAGE,
            )
        elif arguments.start_date is not None and arguments.end_date is not None:
            effective_start_date = arguments.start_date
            effective_end_date = arguments.end_date
        elif settings.start_date is not None and settings.end_date is not None:
            effective_start_date = settings.start_date
            effective_end_date = settings.end_date
        else:
            raise AppError(
                "No date range given and settings.startDate/settings.endDate not configured — "
                "pass --start-date/--end-date, --catch-up, or configure both settings dates."
            )

        target_dates = _date_range(effective_start_date, effective_end_date)

        active_database_factory = database_factory if database_factory is not None else _default_database_factory
        active_archive_reader_factory = archive_reader_factory if archive_reader_factory is not None else _default_archive_reader_factory

        database = active_database_factory(settings.postgres)
        archive_reader = active_archive_reader_factory(settings.postgres)

        succeeded: list[tuple[str, date_type]] = []
        failed: list[tuple[str, date_type]] = []
        try:
            for target_date in target_dates:
                if _is_weekend(target_date):
                    # ingest still fetches/archives weekend dates (IBKR silently returns the prior
                    # trading day's session rather than "no data" for one, so there's real -- if
                    # redundant -- archived content there) but staging that here would just
                    # re-upsert the same bars already staged under their own correct trading date.
                    Logger.diagnostic(
                        f"quant-stage: skipping {target_date.isoformat()} (weekend) -- nothing genuinely new to stage.",
                        category=CATEGORY_STAGE,
                    )
                    continue
                for ticker in tickers:
                    written = _stage_one(settings.providers, archive_reader, database, ticker, target_date)
                    if written is None:
                        failed.append((ticker, target_date))
                    else:
                        succeeded.append((ticker, target_date))
        finally:
            database.close()
            archive_reader.close()

        Logger.info(f"quant-stage: completed ({len(succeeded)} succeeded, {len(failed)} failed).")
        return 1 if failed else 0
    except AppError as error:
        if debug:
            raise
        print(f"quant-stage: error: {error}", file=sys.stderr)
        return 1
    finally:
        Logger.set_logger(None)


if __name__ == "__main__":
    raise SystemExit(main())
