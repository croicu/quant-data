from __future__ import annotations

import json
import time
from datetime import date

import psycopg

from quant_data._internal.contracts import ConnectionTransport, PayloadKind
from quant_data.protocols import LoggingSink

from .diagnostics import Logger
from .errors import AppError

CATEGORY_ARCHIVE = "archive"


class ProviderSourceArchiveWriter:
    """Write-only client for the quant_ingest database -- the append-only record of every provider
    fetch (croicu/quant-data#52). Deliberately a separate class from PostgresDatabase, not a
    method on it: quant_ingest has no relationship to quant_data's star schema (Postgres has no
    cross-database foreign keys), so this class owns its own connection and never touches
    dim_ticker/dim_provider/dim_date at all. This class itself never calls UPDATE/DELETE on
    provider_source_archive -- record_fetch only ever INSERTs. quant_writer's own grants on this
    database originally excluded UPDATE/DELETE entirely (true DB-enforced immutability); DELETE
    was granted afterward at the repo owner's explicit request (a deliberate relaxation, not an
    oversight -- see docs/DATABASE.md), so a row can still be manually cleaned up, but nothing in
    this class's own write path ever does so. UPDATE remains ungranted -- an archived row can be
    removed, never edited in place. archive_coverage itself is a maintained summary table and does
    get updated, same as quant_data's own ingestion_coverage.
    """

    def __init__(self, transport: ConnectionTransport, user: str, password: str, dbname: str, logger: LoggingSink = Logger) -> None:
        self._transport = transport
        self._logger = logger

        open_started = time.perf_counter()
        effective_host, effective_port = transport.open()
        self._logger.perf("transport.open()", time.perf_counter() - open_started)

        if effective_host == "localhost":
            # Same libpq dual-stack pitfall as PostgresDatabase -- see quant-data#19.
            effective_host = "127.0.0.1"

        self._logger.info(f"Connecting to quant_ingest at {effective_host}:{effective_port}/{dbname} as '{user}'...", category=CATEGORY_ARCHIVE)

        connect_started = time.perf_counter()
        try:
            self._connection = psycopg.connect(
                host=effective_host,
                port=effective_port,
                user=user,
                password=password,
                dbname=dbname,
                options="-c TimeZone=UTC",
            )
        except psycopg.Error as error:
            transport.close()
            raise AppError(f"Failed to connect to quant_ingest at {effective_host}:{effective_port}/{dbname}: {error}") from error
        self._logger.perf("psycopg.connect()", time.perf_counter() - connect_started)

        self._logger.info(f"Connected to quant_ingest at {effective_host}:{effective_port}/{dbname}.", category=CATEGORY_ARCHIVE)

    def close(self) -> None:
        self._connection.close()
        self._transport.close()

    def record_fetch(
        self,
        ticker: str,
        provider: str,
        method: str,
        trading_date: date,
        fetch_version: str,
        payload_kind: PayloadKind,
        payload: dict,
    ) -> None:
        """Appends one row to provider_source_archive and coalesces archive_coverage for
        (ticker, provider, method, fetch_version) -- one transaction, so a fetch is never recorded
        without its coverage range also reflecting it. `method` identifies which provider
        call/endpoint produced this payload (croicu/quant-data#60) -- e.g. IBKR's 'TRADES' vs
        'BID_ASK' -- and is passed through as-is, not case-normalized like ticker/provider, since
        IBKR's own whatToShow literals are meaningfully uppercase. Callers should invoke this
        immediately after a provider's fetch_bars() succeeds, before the staging write, so a bug
        in this repo's own parsing/staging code can't lose the fetch."""
        normalized_ticker = ticker.upper()
        normalized_provider = provider.lower()
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO provider_source_archive
                        (ticker, provider, method, trading_date, fetch_version, payload_kind, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (normalized_ticker, normalized_provider, method, trading_date, fetch_version, payload_kind.value, json.dumps(payload)),
                )
                self._record_coverage(cursor, normalized_ticker, normalized_provider, method, fetch_version, trading_date)
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(
                f"Failed to record provider source archive fetch for '{normalized_ticker}' via '{normalized_provider}' ({method}): {error}"
            ) from error

        self._logger.perf(f"record_fetch({normalized_ticker}, {normalized_provider}, {method})", time.perf_counter() - started)

    def mark_covered_without_data(self, ticker: str, provider: str, method: str, trading_date: date, fetch_version: str) -> None:
        """Extends archive_coverage for a (ticker, provider, method, fetch_version) date known in
        advance to never have data (e.g. a weekend for a provider with no IBKR-style silent
        fallback) -- without inserting a provider_source_archive row, since no provider actually
        returned anything to archive. Lets `ingest` skip a wasted API call against a date that can
        never have data while still letting archive_coverage consolidate around it, instead of
        leaving a permanent gap a future run would just re-attempt forever (croicu/quant-data#60)."""
        normalized_ticker = ticker.upper()
        normalized_provider = provider.lower()
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                self._record_coverage(cursor, normalized_ticker, normalized_provider, method, fetch_version, trading_date)
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(
                f"Failed to mark '{normalized_ticker}' via '{normalized_provider}' ({method}) covered without data for {trading_date.isoformat()}: {error}"
            ) from error

        self._logger.perf(f"mark_covered_without_data({normalized_ticker}, {normalized_provider}, {method})", time.perf_counter() - started)

    def _record_coverage(self, cursor: psycopg.Cursor, ticker: str, provider: str, method: str, fetch_version: str, trading_date: date) -> None:
        # Same gaps-and-islands coalescing as PostgresDatabase.record_ingestion_coverage, on plain
        # DATE arithmetic (no surrogate date_id here, unlike quant_data's dim_date) -- keyed by
        # fetch_version too, so bumping a provider's version starts a fresh range rather than
        # silently extending an old one. Also keyed by method: BID_ASK and TRADES can return
        # different bar counts for the same window (confirmed live, croicu/quant-data#60), so
        # coverage for one method says nothing reliable about coverage for another.
        cursor.execute(
            """
            SELECT coverage_id, start_date, end_date
            FROM archive_coverage
            WHERE ticker = %s AND provider = %s AND method = %s AND fetch_version = %s
              AND start_date <= %s + 1 AND end_date >= %s - 1
            ORDER BY start_date
            """,
            (ticker, provider, method, fetch_version, trading_date, trading_date),
        )
        touching_rows = cursor.fetchall()

        already_covered = False
        for _coverage_id, start_date, end_date in touching_rows:
            if start_date <= trading_date <= end_date:
                already_covered = True
                break

        if already_covered:
            return

        if len(touching_rows) == 0:
            cursor.execute(
                "INSERT INTO archive_coverage (ticker, provider, method, fetch_version, start_date, end_date) VALUES (%s, %s, %s, %s, %s, %s)",
                (ticker, provider, method, fetch_version, trading_date, trading_date),
            )
            return

        all_starts: list[date] = [trading_date]
        all_ends: list[date] = [trading_date]
        for _coverage_id, start_date, end_date in touching_rows:
            all_starts.append(start_date)
            all_ends.append(end_date)
        new_start = min(all_starts)
        new_end = max(all_ends)

        keep_coverage_id = touching_rows[0][0]
        for row in touching_rows[1:]:
            cursor.execute("DELETE FROM archive_coverage WHERE coverage_id = %s", (row[0],))
        cursor.execute(
            "UPDATE archive_coverage SET start_date = %s, end_date = %s, updated_at = CURRENT_TIMESTAMP WHERE coverage_id = %s",
            (new_start, new_end, keep_coverage_id),
        )


class ProviderSourceArchiveReader:
    """Read-only client for the quant_ingest database -- used by `stage` (croicu/quant-data#56) to
    turn archived fetches back into staging_market_data_1min rows. Deliberately a separate class
    from ProviderSourceArchiveWriter, not a shared read/write class: the writer's own docstring
    is explicit about being write-only, matching quant_writer's DB-level grants (INSERT only, plus
    the later, deliberate DELETE relaxation -- never intended to also mean 'and reads'). Connection
    setup mirrors ProviderSourceArchiveWriter's exactly."""

    def __init__(self, transport: ConnectionTransport, user: str, password: str, dbname: str, logger: LoggingSink = Logger) -> None:
        self._transport = transport
        self._logger = logger

        open_started = time.perf_counter()
        effective_host, effective_port = transport.open()
        self._logger.perf("transport.open()", time.perf_counter() - open_started)

        if effective_host == "localhost":
            # Same libpq dual-stack pitfall as PostgresDatabase -- see quant-data#19.
            effective_host = "127.0.0.1"

        self._logger.info(f"Connecting to quant_ingest at {effective_host}:{effective_port}/{dbname} as '{user}'...", category=CATEGORY_ARCHIVE)

        connect_started = time.perf_counter()
        try:
            self._connection = psycopg.connect(
                host=effective_host,
                port=effective_port,
                user=user,
                password=password,
                dbname=dbname,
                options="-c TimeZone=UTC",
            )
        except psycopg.Error as error:
            transport.close()
            raise AppError(f"Failed to connect to quant_ingest at {effective_host}:{effective_port}/{dbname}: {error}") from error
        self._logger.perf("psycopg.connect()", time.perf_counter() - connect_started)

        self._logger.info(f"Connected to quant_ingest at {effective_host}:{effective_port}/{dbname}.", category=CATEGORY_ARCHIVE)

    def close(self) -> None:
        self._connection.close()
        self._transport.close()

    def fetch_latest_bars(self, ticker: str, provider: str, method: str, trading_date: date) -> tuple[PayloadKind, dict] | None:
        """The most recently archived fetch for (ticker, provider, method, trading_date), or None
        if nothing's been archived for that key. provider_source_archive has no uniqueness
        constraint on that key (a re-fetch is a new row, never an upsert -- see migrations/
        quant_ingest/001_init_provider_source_archive.sql), so this picks the row with the latest
        fetched_at as the one worth staging. `method` must be given explicitly (croicu/quant-data#60)
        rather than defaulting to "whatever's latest for this provider": once a provider archives
        more than one method (e.g. IBKR's TRADES and BID_ASK), an unfiltered "latest fetched_at"
        query could silently pick a non-OHLCV call's row."""
        normalized_ticker = ticker.upper()
        normalized_provider = provider.lower()
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT payload_kind, payload
                    FROM provider_source_archive
                    WHERE ticker = %s AND provider = %s AND method = %s AND trading_date = %s
                    ORDER BY fetched_at DESC
                    LIMIT 1
                    """,
                    (normalized_ticker, normalized_provider, method, trading_date),
                )
                row = cursor.fetchone()
        except psycopg.Error as error:
            raise AppError(f"Failed to read provider source archive for '{normalized_ticker}' via '{normalized_provider}' ({method}): {error}") from error

        self._logger.perf(f"fetch_latest_bars({normalized_ticker}, {normalized_provider}, {method})", time.perf_counter() - started)

        if row is None:
            return None
        payload_kind_value, payload = row
        return PayloadKind(payload_kind_value), payload
