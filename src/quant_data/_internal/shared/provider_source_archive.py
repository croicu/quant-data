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
        trading_date: date,
        fetch_version: str,
        payload_kind: PayloadKind,
        payload: dict,
    ) -> None:
        """Appends one row to provider_source_archive and coalesces archive_coverage for
        (ticker, provider, fetch_version) -- one transaction, so a fetch is never recorded without
        its coverage range also reflecting it. Callers should invoke this immediately after a
        provider's fetch_bars() succeeds, before the staging write, so a bug in this repo's own
        parsing/staging code can't lose the fetch."""
        normalized_ticker = ticker.upper()
        normalized_provider = provider.lower()
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO provider_source_archive
                        (ticker, provider, trading_date, fetch_version, payload_kind, payload)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (normalized_ticker, normalized_provider, trading_date, fetch_version, payload_kind.value, json.dumps(payload)),
                )
                self._record_coverage(cursor, normalized_ticker, normalized_provider, fetch_version, trading_date)
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to record provider source archive fetch for '{normalized_ticker}' via '{normalized_provider}': {error}") from error

        self._logger.perf(f"record_fetch({normalized_ticker}, {normalized_provider})", time.perf_counter() - started)

    def _record_coverage(self, cursor: psycopg.Cursor, ticker: str, provider: str, fetch_version: str, trading_date: date) -> None:
        # Same gaps-and-islands coalescing as PostgresDatabase.record_ingestion_coverage, on plain
        # DATE arithmetic (no surrogate date_id here, unlike quant_data's dim_date) -- keyed by
        # fetch_version too, so bumping a provider's version starts a fresh range rather than
        # silently extending an old one.
        cursor.execute(
            """
            SELECT coverage_id, start_date, end_date
            FROM archive_coverage
            WHERE ticker = %s AND provider = %s AND fetch_version = %s
              AND start_date <= %s + 1 AND end_date >= %s - 1
            ORDER BY start_date
            """,
            (ticker, provider, fetch_version, trading_date, trading_date),
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
                "INSERT INTO archive_coverage (ticker, provider, fetch_version, start_date, end_date) VALUES (%s, %s, %s, %s, %s)",
                (ticker, provider, fetch_version, trading_date, trading_date),
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
