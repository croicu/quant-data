from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from quant_data._internal.contracts import IntraDayProvider
from quant_data._internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.postgres import PostgresDatabase
from quant_data._internal.shared.providers.ibkr import CATEGORY_IBKR, IBKRIntraDay
from quant_data._internal.shared.providers.yfinance import CATEGORY_YFINANCE, YahooFinanceIntraDay
from quant_data._internal.shared.settings import PostgresSettings, RateLimitSettings, Settings
from quant_data._internal.shared.transports import resolve_transport
from quant_data.protocols import DataQuality

from .rate_limiter import RateLimiter

CATEGORY_INGEST = "ingest"

# Per-provider fetch-failure log category, so a Yahoo vs. IBKR fetch problem stays filterable
# apart from a Postgres write problem -- same reasoning _ingest_one's own comment gives for
# keeping fetch/write failures logged separately in the first place.
_FETCH_FAILURE_CATEGORY: dict[str, str] = {
    "yfinance": CATEGORY_YFINANCE,
    "ibkr": CATEGORY_IBKR,
}


@dataclass
class CliArguments:
    ticker: str | None = None
    start_date: date_type | None = None
    end_date: date_type | None = None
    catch_up: bool = False
    backfill: bool = False
    debug: bool = False


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-ingest",
        usage="quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up | --backfill] [--ticker TICKER] [--debug]",
        description=(
            "Fetch 1-minute OHLCV bars for one ticker (with --ticker) or every ticker in "
            "settings.tickers (without --ticker), over an inclusive date range (--start-date alone "
            "means a single day; add --end-date for a range; omit both to use "
            "settings.startDate/settings.endDate), and store them in quant-data's warehouse. "
            "--catch-up re-fetches the trailing settings.catchUpLookbackDays days instead, to fill "
            "in days a prior run only partially ingested. --backfill instead walks each ticker "
            "backward from its current earliest covered date toward dataset_inception, one "
            "settings.backfillChunkDays chunk per ticker per invocation."
        ),
    )

    parser.add_argument("--ticker", default=None, help="single ticker to fetch, e.g. AAPL; omit to use settings.tickers")
    parser.add_argument("--start-date", default=None, help="first trading date to fetch, YYYY-MM-DD; omit to use settings.startDate")
    parser.add_argument("--end-date", default=None, help="last trading date to fetch (inclusive), YYYY-MM-DD; omit to default to --start-date (a single day)")
    parser.add_argument(
        "--catch-up",
        action="store_true",
        default=False,
        help="re-fetch the trailing settings.catchUpLookbackDays days (excluding today) instead of a date range",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        default=False,
        help="walk each ticker backward from its current earliest covered date toward dataset_inception, one settings.backfillChunkDays chunk per invocation",
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

    if args.backfill and (args.start_date is not None or args.end_date is not None):
        parser.error("--backfill cannot be combined with --start-date/--end-date.")

    if args.backfill and args.catch_up:
        parser.error("--backfill cannot be combined with --catch-up.")

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

    return CliArguments(ticker=args.ticker, start_date=start_date, end_date=end_date, catch_up=args.catch_up, backfill=args.backfill, debug=args.debug)


def _date_range(start_date: date_type, end_date: date_type) -> list[date_type]:
    dates: list[date_type] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)
    return dates


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


def _build_provider(name: str, settings: Settings) -> IntraDayProvider:
    if name == "yfinance":
        return YahooFinanceIntraDay()
    if name == "ibkr":
        return IBKRIntraDay(host=settings.ibkr.host, port=settings.ibkr.port, client_id=settings.ibkr.client_id)
    raise AppError(f"Unknown provider '{name}' in settings.providers -- expected one of: yfinance, ibkr.")


def _default_providers(settings: Settings) -> dict[str, IntraDayProvider]:
    providers: dict[str, IntraDayProvider] = {}
    for name in settings.providers:
        providers[name] = _build_provider(name, settings)
    return providers


def _rate_limit_for(name: str, settings: Settings) -> RateLimitSettings | None:
    if name == "ibkr":
        return settings.ibkr.rate_limit
    if name == "yfinance":
        return settings.yfinance.rate_limit
    return None


def _default_rate_limiters(settings: Settings) -> dict[str, RateLimiter]:
    # Sits between the orchestration loop and IntraDayProvider.fetch_bars, not inside any one
    # provider implementation -- pacing is a property of reaching a specific external service
    # (croicu/quant-data#28). Unspecified (None) means unlimited, so a provider with no configured
    # limit gets no entry here at all.
    rate_limiters: dict[str, RateLimiter] = {}
    for name in settings.providers:
        rate_limit = _rate_limit_for(name, settings)
        if rate_limit is not None:
            rate_limiters[name] = RateLimiter(requests_per_window=rate_limit.requests_per_window, window_seconds=rate_limit.window_seconds)
    return rate_limiters


def _ingest_one(
    providers: dict[str, IntraDayProvider],
    database: PostgresDatabase,
    rate_limiters: dict[str, RateLimiter],
    ticker: str,
    target_date: date_type,
) -> int | None:
    # Fetch and write failures are logged separately (rather than one catch-all at the call
    # site) so the log category actually reflects where the failure came from -- both raise the
    # same AppError type, so a single shared catch couldn't tell a fetch problem (e.g. a weekend,
    # or a bad ticker -- indistinguishable from each other today) apart from a Postgres write
    # problem (e.g. a date outside the populated dim_date range). Each configured provider writes
    # independently to staging_market_data_1min, blind to what other providers wrote for the same
    # bar -- one provider failing (bad ticker on that source, gateway unreachable, ...) doesn't
    # stop the others from still writing their own data for this (ticker, date).
    Logger.diagnostic(
        f"quant-ingest: starting {ticker.upper()} on {target_date.isoformat()}.",
        category=CATEGORY_INGEST,
    )

    total_written = 0
    total_incomplete = 0
    any_provider_succeeded = False

    for provider_name, provider in providers.items():
        rate_limiter = rate_limiters.get(provider_name)
        if rate_limiter is not None:
            rate_limiter.acquire()

        try:
            bars = provider.fetch_bars(ticker, target_date)
        except AppError as error:
            Logger.warning(
                f"quant-ingest: failed to fetch '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=_FETCH_FAILURE_CATEGORY.get(provider_name, CATEGORY_INGEST),
            )
            continue

        try:
            written = database.write_staging_bars(provider_name, bars)
        except AppError as error:
            Logger.warning(
                f"quant-ingest: failed to write staging bars for '{ticker.upper()}' on {target_date.isoformat()} via '{provider_name}': {error}",
                category=CATEGORY_INGEST,
            )
            continue

        any_provider_succeeded = True
        total_written += written
        for bar in bars:
            if bar.data_quality != DataQuality.ACCEPTED:
                total_incomplete += 1

    if not any_provider_succeeded:
        return None

    Logger.info(
        f"quant-ingest: wrote {total_written} staging bars for {ticker.upper()} on {target_date.isoformat()} "
        f"across {len(providers)} provider(s) ({total_incomplete} incomplete).",
        category=CATEGORY_INGEST,
    )
    return total_written


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    providers: dict[str, IntraDayProvider] | None = None,
    database_factory: Callable[[PostgresSettings], PostgresDatabase] | None = None,
    today: Callable[[], date_type] | None = None,
    rate_limiters: dict[str, RateLimiter] | None = None,
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

        active_providers = providers if providers is not None else _default_providers(settings)

        # Connect once per batch (see IntraDayProvider.connect()'s own docstring) rather than
        # per-call -- a provider that fails to connect (e.g. IBKR Gateway not running) is dropped
        # for the rest of this run instead of aborting it, matching the per-(ticker, date)
        # fetch/write tolerance below.
        connected_providers: dict[str, IntraDayProvider] = {}
        for provider_name, provider_instance in active_providers.items():
            try:
                provider_instance.connect()
            except AppError as error:
                Logger.warning(
                    f"quant-ingest: failed to connect provider '{provider_name}': {error}",
                    category=_FETCH_FAILURE_CATEGORY.get(provider_name, CATEGORY_INGEST),
                )
                continue
            connected_providers[provider_name] = provider_instance

        if not connected_providers:
            raise AppError("No configured provider could connect -- check settings.providers and each provider's connection settings.")

        active_database_factory = database_factory if database_factory is not None else _default_database_factory
        active_rate_limiters = rate_limiters if rate_limiters is not None else _default_rate_limiters(settings)

        database = active_database_factory(settings.postgres)
        succeeded: list[tuple[str, date_type]] = []
        failed: list[tuple[str, date_type]] = []
        try:
            if arguments.backfill:
                active_today = date_type.today() if today is None else today()
                inception_date = database.fetch_dataset_inception_date()

                for ticker in tickers:
                    earliest_covered = database.fetch_earliest_covered_date(ticker)
                    if earliest_covered is not None and earliest_covered <= inception_date:
                        Logger.info(
                            f"quant-ingest --backfill: '{ticker.upper()}' already covers back to inception_date ({inception_date.isoformat()}); nothing to do.",
                            category=CATEGORY_INGEST,
                        )
                        continue

                    # A ticker with no existing coverage at all bootstraps from today backward,
                    # using the same "excludes today" convention as --catch-up above -- this is
                    # the same formula as the normal case, just with today standing in for
                    # earliest_covered when there's no prior coverage to walk back from.
                    reference_date = earliest_covered if earliest_covered is not None else active_today
                    chunk_end_date = reference_date - timedelta(days=1)
                    chunk_start_date = max(inception_date, chunk_end_date - timedelta(days=settings.backfill_chunk_days - 1))

                    Logger.info(
                        f"quant-ingest --backfill: '{ticker.upper()}' fetching {chunk_start_date.isoformat()} to {chunk_end_date.isoformat()}.",
                        category=CATEGORY_INGEST,
                    )
                    for target_date in _date_range(chunk_start_date, chunk_end_date):
                        written = _ingest_one(connected_providers, database, active_rate_limiters, ticker, target_date)
                        if written is None:
                            failed.append((ticker, target_date))
                        else:
                            succeeded.append((ticker, target_date))
            else:
                if arguments.catch_up:
                    active_today = date_type.today() if today is None else today()
                    effective_end_date = active_today - timedelta(days=1)
                    effective_start_date = active_today - timedelta(days=settings.catch_up_lookback_days)
                    Logger.info(
                        f"quant-ingest: catch-up mode, re-fetching {effective_start_date.isoformat()} to {effective_end_date.isoformat()}.",
                        category=CATEGORY_INGEST,
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
                        "pass --start-date/--end-date, --catch-up, --backfill, or configure both settings dates."
                    )

                target_dates = _date_range(effective_start_date, effective_end_date)

                for target_date in target_dates:
                    for ticker in tickers:
                        written = _ingest_one(connected_providers, database, active_rate_limiters, ticker, target_date)
                        if written is None:
                            failed.append((ticker, target_date))
                        else:
                            succeeded.append((ticker, target_date))
        finally:
            database.close()
            for provider_instance in connected_providers.values():
                provider_instance.close()

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
