from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import psycopg

from quant_data._internal.contracts import ConnectionTransport
from quant_data.protocols import OHLCV, DataQuality, LoggingSink, PendingResolutionBar, ProviderRole, RejectedWhistleblowerBar

from .diagnostics import Logger
from .errors import AppError, DateOutOfRangeError

CATEGORY_POSTGRES = "postgres"


# Plain data shapes for reconcile's read/write needs -- deliberately not aware of any
# reconciliation business concept (candidate/whistleblower, tiers, ...); that domain model lives
# in reconcile.algorithm. quant_data._internal must never import from reconcile/ingest (rule 8's
# acyclic dependency graph) -- reconcile depends on this module, not the other way around, same
# direction ingest already uses.
@dataclass
class ProviderRow:
    provider_id: int
    name: str
    role: str


@dataclass
class FieldGroupRow:
    field_group_id: int
    name: str


@dataclass
class FieldRow:
    field_id: int
    name: str


@dataclass
class IngestionCoverageRow:
    ticker_id: int
    provider_id: int
    start_date_id: int
    end_date_id: int


@dataclass
class StagingRow:
    ticker_id: int
    date_id: int
    time_id: int
    timestamp: datetime
    provider_id: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    data_quality: str


@dataclass
class DisagreementStatsRow:
    provider_id: int
    ticker_id: int
    field_id: int
    sample_count: int
    running_mean: float
    running_m2: float


@dataclass
class DataQualityThresholdRow:
    provider_id: int
    ticker_id: int
    k_reversal_oc: float
    k_trend_oc: float
    k_reversal_hl: float
    k_trend_hl: float


class PostgresDatabase:
    """Concrete MarketDataProvider implementation, plus a write path used only by ingest.

    Single connection per invocation (no pooling) — one instance is created per CLI run and
    reused for every query/write during that run, then closed when the process exits.

    Agnostic of how Postgres is actually reached: `transport` resolves that (direct connect vs.
    an SSH tunnel) so this class never needs to know or care about the concrete hosting choice —
    see quant_data._internal.shared.transports.

    `logger` defaults to quant_data's own private `Logger` class (its methods are all
    `@staticmethod`s, so using the class itself as the default value behaves identically to the
    direct static calls this replaced). A host application can inject its own `LoggingSink`
    instead, so quant_data's internal logging lands in the host's own log stream — see
    quant_data#20.
    """

    def __init__(self, transport: ConnectionTransport, user: str, password: str, dbname: str, logger: LoggingSink = Logger) -> None:
        self._transport = transport
        self._logger = logger

        open_started = time.perf_counter()
        effective_host, effective_port = transport.open()
        self._logger.perf("transport.open()", time.perf_counter() - open_started)

        if effective_host == "localhost":
            # psycopg/libpq resolves the bare hostname "localhost" as dual-stack and can fall
            # back from an unreachable IPv6 loopback to IPv4 with a very long internal timeout
            # (~130s observed) instead of connecting immediately -- the literal address doesn't
            # have this ambiguity. Normalized here (not just in SshTunnelTransport) since any
            # transport/caller could hand back this same problem hostname. See quant-data#19.
            effective_host = "127.0.0.1"

        self._logger.info(f"Connecting to Postgres at {effective_host}:{effective_port}/{dbname} as '{user}'...", category=CATEGORY_POSTGRES)

        connect_started = time.perf_counter()
        try:
            self._connection = psycopg.connect(
                host=effective_host,
                port=effective_port,
                user=user,
                password=password,
                dbname=dbname,
                # fact_market_data_1min.timestamp is TIMESTAMP WITHOUT TIME ZONE, but every OHLCV
                # we bind is tz-aware UTC. Without pinning the session, Postgres treats that as a
                # timestamptz and implicitly casts it down using the connection's TimeZone GUC —
                # which defaults to the server's local zone, not UTC — silently shifting the
                # stored wall-clock value. See quant-data#9.
                options="-c TimeZone=UTC",
            )
        except psycopg.Error as error:
            transport.close()
            raise AppError(f"Failed to connect to Postgres at {effective_host}:{effective_port}/{dbname}: {error}") from error
        self._logger.perf("psycopg.connect()", time.perf_counter() - connect_started)

        self._logger.info(f"Connected to Postgres at {effective_host}:{effective_port}/{dbname}.", category=CATEGORY_POSTGRES)

    def close(self) -> None:
        self._connection.close()
        self._transport.close()

    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        normalized_ticker = ticker.upper()

        query = """
            SELECT t.ticker, f.timestamp, f.open, f.high, f.low, f.close, f.volume, f.data_quality
            FROM fact_market_data_1min f
            JOIN dim_ticker t ON t.ticker_id = f.ticker_id
            JOIN dim_date d ON d.date_id = f.date_id
            WHERE t.ticker = %s AND d.date BETWEEN %s AND %s
            ORDER BY f.timestamp
        """

        started = time.perf_counter()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (normalized_ticker, start_date, end_date))
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch bars for '{normalized_ticker}' from {start_date} to {end_date}: {error}") from error
        self._logger.perf(f"fetch_bars({normalized_ticker}, {start_date.isoformat()}..{end_date.isoformat()})", time.perf_counter() - started)

        bars: list[OHLCV] = []
        for row in rows:
            row_ticker, row_timestamp, row_open, row_high, row_low, row_close, row_volume, row_data_quality = row
            bar = OHLCV(
                ticker=row_ticker,
                timestamp=row_timestamp,
                open=float(row_open),
                high=float(row_high),
                low=float(row_low),
                close=float(row_close),
                volume=int(row_volume),
                data_quality=DataQuality(row_data_quality),
            )
            bars.append(bar)

        return bars

    def fetch_pending_resolution_bars(self, ticker: str, start_date: date, end_date: date) -> list[PendingResolutionBar]:
        normalized_ticker = ticker.upper()

        query = """
            SELECT t.ticker, s.timestamp, g.name, p.name, p.role, s.open, s.high, s.low, s.close, s.volume, s.data_quality
            FROM fact_pending_manual_resolution fpmr
            JOIN dim_ticker t ON t.ticker_id = fpmr.ticker_id
            JOIN dim_date d ON d.date_id = fpmr.date_id
            JOIN dim_field_group g ON g.field_group_id = fpmr.field_group_id
            JOIN staging_market_data_1min s
                ON s.ticker_id = fpmr.ticker_id AND s.date_id = fpmr.date_id AND s.time_id = fpmr.time_id
            JOIN dim_provider p ON p.provider_id = s.provider_id
            WHERE t.ticker = %s AND d.date BETWEEN %s AND %s
            ORDER BY s.timestamp, p.name
        """

        started = time.perf_counter()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (normalized_ticker, start_date, end_date))
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch pending-resolution bars for '{normalized_ticker}' from {start_date} to {end_date}: {error}") from error
        self._logger.perf(
            f"fetch_pending_resolution_bars({normalized_ticker}, {start_date.isoformat()}..{end_date.isoformat()})", time.perf_counter() - started
        )

        candidates: list[PendingResolutionBar] = []
        for row in rows:
            row_ticker, row_timestamp, row_field_group, row_provider, row_role, row_open, row_high, row_low, row_close, row_volume, row_data_quality = row
            bar = OHLCV(
                ticker=row_ticker,
                timestamp=row_timestamp,
                open=float(row_open),
                high=float(row_high),
                low=float(row_low),
                close=float(row_close),
                volume=int(row_volume),
                data_quality=DataQuality(row_data_quality),
            )
            candidates.append(PendingResolutionBar(field_group=row_field_group, provider=row_provider, role=ProviderRole(row_role), bar=bar))

        return candidates

    def fetch_rejected_whistleblower_bars(self, ticker: str, start_date: date, end_date: date) -> list[RejectedWhistleblowerBar]:
        """Every staging_market_data_1min row from a whistleblower provider with
        data_quality='rejected' -- distinct from fetch_pending_resolution_bars, since a rejected
        whistleblower value with an accepted candidate auto-resolves via Tier 1 completeness and
        never reaches fact_pending_manual_resolution at all. Finds it regardless of resolution
        outcome because whistleblower rows are never purged (see purge_staging_bar)."""
        normalized_ticker = ticker.upper()

        query = """
            SELECT t.ticker, s.timestamp, p.name, s.open, s.high, s.low, s.close, s.volume, s.data_quality
            FROM staging_market_data_1min s
            JOIN dim_ticker t ON t.ticker_id = s.ticker_id
            JOIN dim_date d ON d.date_id = s.date_id
            JOIN dim_provider p ON p.provider_id = s.provider_id
            WHERE t.ticker = %s AND d.date BETWEEN %s AND %s
              AND p.role = %s AND s.data_quality = %s
            ORDER BY s.timestamp
        """

        started = time.perf_counter()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (normalized_ticker, start_date, end_date, ProviderRole.WHISTLEBLOWER.value, DataQuality.REJECTED.value))
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch rejected whistleblower bars for '{normalized_ticker}' from {start_date} to {end_date}: {error}") from error
        self._logger.perf(
            f"fetch_rejected_whistleblower_bars({normalized_ticker}, {start_date.isoformat()}..{end_date.isoformat()})", time.perf_counter() - started
        )

        result: list[RejectedWhistleblowerBar] = []
        for row in rows:
            row_ticker, row_timestamp, row_provider, row_open, row_high, row_low, row_close, row_volume, row_data_quality = row
            bar = OHLCV(
                ticker=row_ticker,
                timestamp=row_timestamp,
                open=float(row_open),
                high=float(row_high),
                low=float(row_low),
                close=float(row_close),
                volume=int(row_volume),
                data_quality=DataQuality(row_data_quality),
            )
            result.append(RejectedWhistleblowerBar(provider=row_provider, bar=bar))

        return result

    def _resolve_dimension_ids(self, cursor: psycopg.Cursor, bar: OHLCV) -> tuple[int, int, int]:
        normalized_ticker = bar.ticker.upper()

        cursor.execute(
            """
            INSERT INTO dim_ticker (ticker) VALUES (%s)
            ON CONFLICT (ticker) DO UPDATE SET ticker = EXCLUDED.ticker
            RETURNING ticker_id
            """,
            (normalized_ticker,),
        )
        ticker_id = cursor.fetchone()[0]

        cursor.execute("SELECT date_id FROM dim_date WHERE date = %s", (bar.timestamp.date(),))
        date_row = cursor.fetchone()
        if date_row is None:
            raise DateOutOfRangeError(f"No dim_date row for {bar.timestamp.date()} — outside the populated date range.")
        date_id = date_row[0]

        cursor.execute(
            "SELECT time_id FROM dim_time WHERE hour = %s AND minute = %s",
            (bar.timestamp.hour, bar.timestamp.minute),
        )
        time_row = cursor.fetchone()
        if time_row is None:
            raise AppError(f"No dim_time row for {bar.timestamp.hour:02d}:{bar.timestamp.minute:02d}.")
        time_id = time_row[0]

        return ticker_id, date_id, time_id

    def write_bars(self, bars: list[OHLCV]) -> int:
        written = 0
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                for bar in bars:
                    ticker_id, date_id, time_id = self._resolve_dimension_ids(cursor, bar)

                    cursor.execute(
                        """
                        INSERT INTO fact_market_data_1min
                            (ticker_id, date_id, time_id, open, high, low, close, volume, timestamp, data_quality)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticker_id, date_id, time_id) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            timestamp = EXCLUDED.timestamp,
                            data_quality = EXCLUDED.data_quality
                        """,
                        (
                            ticker_id,
                            date_id,
                            time_id,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume,
                            bar.timestamp,
                            bar.data_quality.value,
                        ),
                    )
                    written += 1
            self._connection.commit()
        except AppError:
            self._connection.rollback()
            raise
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to write bars: {error}") from error

        self._logger.perf(f"write_bars({written} bars)", time.perf_counter() - started)
        return written

    def write_staging_bars(self, provider_name: str, bars: list[OHLCV]) -> int:
        normalized_provider_name = provider_name.lower()
        written = 0
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT provider_id FROM dim_provider WHERE name = %s", (normalized_provider_name,))
                provider_row = cursor.fetchone()
                if provider_row is None:
                    raise AppError(f"No dim_provider row for '{normalized_provider_name}' -- expected one of the seeded provider names.")
                provider_id = provider_row[0]

                for bar in bars:
                    ticker_id, date_id, time_id = self._resolve_dimension_ids(cursor, bar)

                    cursor.execute(
                        """
                        INSERT INTO staging_market_data_1min
                            (provider_id, ticker_id, date_id, time_id, open, high, low, close, volume, timestamp, data_quality)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (provider_id, ticker_id, date_id, time_id) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            timestamp = EXCLUDED.timestamp,
                            data_quality = EXCLUDED.data_quality
                        """,
                        (
                            provider_id,
                            ticker_id,
                            date_id,
                            time_id,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume,
                            bar.timestamp,
                            bar.data_quality.value,
                        ),
                    )
                    written += 1
            self._connection.commit()
        except AppError:
            self._connection.rollback()
            raise
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to write staging bars: {error}") from error

        self._logger.perf(f"write_staging_bars({normalized_provider_name}, {written} bars)", time.perf_counter() - started)
        return written

    def fetch_dataset_inception_date(self) -> date:
        """The date the dataset is meant to start from -- --backfill's target. Raises rather than
        returning None when the table is empty (croicu/quant-data#28's schema-only slice
        deliberately left it that way): a missing inception_date is a real configuration gap
        --backfill cannot proceed past, not a value to silently default around."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT inception_date FROM dataset_inception WHERE id = 1")
                row = cursor.fetchone()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch dataset_inception: {error}") from error

        if row is None:
            raise AppError(
                "dataset_inception is empty -- insert the dataset's actual inception_date before running "
                "--backfill (see migrations/007_add_dim_field_and_dataset_inception.sql)."
            )
        return row[0]

    def fetch_earliest_covered_date(self, ticker: str) -> date | None:
        """MIN(date) for this ticker across staging_market_data_1min and fact_market_data_1min
        combined -- --backfill's "how far back does this ticker already reach" query. None means
        the ticker has never been ingested at all (--backfill auto-bootstraps from today in that
        case; see ingest/cli.py's --backfill branch)."""
        normalized_ticker = ticker.upper()
        query = """
            SELECT MIN(d.date)
            FROM dim_date d
            WHERE d.date_id IN (
                SELECT s.date_id FROM staging_market_data_1min s
                JOIN dim_ticker t ON t.ticker_id = s.ticker_id
                WHERE t.ticker = %s
                UNION
                SELECT f.date_id FROM fact_market_data_1min f
                JOIN dim_ticker t ON t.ticker_id = f.ticker_id
                WHERE t.ticker = %s
            )
        """
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (normalized_ticker, normalized_ticker))
                row = cursor.fetchone()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch earliest covered date for '{normalized_ticker}': {error}") from error

        if row is None or row[0] is None:
            return None
        return row[0]

    # -- Reconcile-facing reads/writes below. quant-reconcile is the only caller; PostgresDatabase
    # stays the single connection-owning class for every internal purpose (MarketDataProvider
    # reads, ingest's writes, reconcile's reads/writes) rather than introducing a second
    # connection-management implementation for a third caller. --

    def fetch_dim_providers(self) -> list[ProviderRow]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT provider_id, name, role FROM dim_provider")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch dim_provider: {error}") from error

        result: list[ProviderRow] = []
        for provider_id, name, role in rows:
            result.append(ProviderRow(provider_id=provider_id, name=name, role=role))
        return result

    def fetch_dim_field_groups(self) -> list[FieldGroupRow]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT field_group_id, name FROM dim_field_group")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch dim_field_group: {error}") from error

        result: list[FieldGroupRow] = []
        for field_group_id, name in rows:
            result.append(FieldGroupRow(field_group_id=field_group_id, name=name))
        return result

    def fetch_dim_fields(self) -> list[FieldRow]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT field_id, name FROM dim_field")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch dim_field: {error}") from error

        result: list[FieldRow] = []
        for field_id, name in rows:
            result.append(FieldRow(field_id=field_id, name=name))
        return result

    def fetch_provider_pair_disagreement(self) -> list[DisagreementStatsRow]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT provider_id, ticker_id, field_id, sample_count, running_mean, running_m2 FROM provider_pair_disagreement")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch provider_pair_disagreement: {error}") from error

        result: list[DisagreementStatsRow] = []
        for provider_id, ticker_id, field_id, sample_count, running_mean, running_m2 in rows:
            result.append(
                DisagreementStatsRow(
                    provider_id=provider_id,
                    ticker_id=ticker_id,
                    field_id=field_id,
                    sample_count=sample_count,
                    running_mean=float(running_mean),
                    running_m2=float(running_m2),
                )
            )
        return result

    def fetch_data_quality_thresholds(self) -> list[DataQualityThresholdRow]:
        """Every deliberately-tuned (provider, ticker) override for the outlier-detection check
        (reconcile/outlier_detection.py) -- small, bulk-fetched once per reconcile run. A
        (provider, ticker) with no row here uses the module's own DEFAULT_K_* constants."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT provider_id, ticker_id, k_reversal_oc, k_trend_oc, k_reversal_hl, k_trend_hl FROM data_quality_thresholds")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch data_quality_thresholds: {error}") from error

        result: list[DataQualityThresholdRow] = []
        for provider_id, ticker_id, k_reversal_oc, k_trend_oc, k_reversal_hl, k_trend_hl in rows:
            result.append(
                DataQualityThresholdRow(
                    provider_id=provider_id,
                    ticker_id=ticker_id,
                    k_reversal_oc=float(k_reversal_oc),
                    k_trend_oc=float(k_trend_oc),
                    k_reversal_hl=float(k_reversal_hl),
                    k_trend_hl=float(k_trend_hl),
                )
            )
        return result

    def fetch_whistleblower_accepted_staging_rows(self) -> list[StagingRow]:
        """Every staging_market_data_1min row from a whistleblower provider with
        data_quality='accepted' -- the candidate pool for the outlier-detection check. Scoped to
        'accepted' only: an already-incomplete row (e.g. a real zero-volume placeholder) has no
        meaningful OHLC pattern to judge plausibility from, and re-flagging an already-rejected
        row is a no-op. Bulk-fetched once per reconcile run; the caller groups by (ticker_id,
        date_id) and walks each day in time_id order to build windows in memory."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.ticker_id, s.date_id, s.time_id, s.timestamp, s.provider_id,
                           s.open, s.high, s.low, s.close, s.volume, s.data_quality
                    FROM staging_market_data_1min s
                    JOIN dim_provider p ON p.provider_id = s.provider_id
                    WHERE p.role = %s AND s.data_quality = %s
                    ORDER BY s.ticker_id, s.date_id, s.time_id
                    """,
                    (ProviderRole.WHISTLEBLOWER.value, DataQuality.ACCEPTED.value),
                )
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch whistleblower accepted staging rows: {error}") from error

        result: list[StagingRow] = []
        for ticker_id, date_id, time_id, timestamp, provider_id, open_, high, low, close, volume, data_quality in rows:
            result.append(
                StagingRow(
                    ticker_id=ticker_id,
                    date_id=date_id,
                    time_id=time_id,
                    timestamp=timestamp,
                    provider_id=provider_id,
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume),
                    data_quality=data_quality,
                )
            )
        return result

    def mark_staging_bars_rejected(self, keys: list[tuple[int, int, int, int]]) -> None:
        """Sets data_quality='rejected' for every (provider_id, ticker_id, date_id, time_id) key
        given, in one transaction (one commit regardless of row count -- see croicu/quant-data#30
        for why that matters at scale)."""
        if not keys:
            return
        try:
            with self._connection.cursor() as cursor:
                for provider_id, ticker_id, date_id, time_id in keys:
                    cursor.execute(
                        """
                        UPDATE staging_market_data_1min
                        SET data_quality = %s
                        WHERE provider_id = %s AND ticker_id = %s AND date_id = %s AND time_id = %s
                        """,
                        (DataQuality.REJECTED.value, provider_id, ticker_id, date_id, time_id),
                    )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to mark {len(keys)} staging bar(s) rejected: {error}") from error

    def fetch_ingestion_coverage(self) -> list[IngestionCoverageRow]:
        """Every contiguous ingested date range for every (ticker, provider) pair
        (croicu/quant-data#31) -- small, bulk-fetched once per reconcile run (no round trip per
        bar), used to decide whether a bar's missing whistleblower row means "confirmed absent"
        (its date range was ingested) or "not ingested yet" (must still wait)."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT ticker_id, provider_id, start_date_id, end_date_id FROM ingestion_coverage")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch ingestion_coverage: {error}") from error

        result: list[IngestionCoverageRow] = []
        for ticker_id, provider_id, start_date_id, end_date_id in rows:
            result.append(IngestionCoverageRow(ticker_id=ticker_id, provider_id=provider_id, start_date_id=start_date_id, end_date_id=end_date_id))
        return result

    def fetch_staging_rows_for_reconciliation(self, expected_provider_names: list[str], required_provider_names: list[str]) -> list[StagingRow]:
        """Every staging_market_data_1min row belonging to a bar where every name in
        required_provider_names has a row -- eligibility requires only the candidate providers
        (croicu/quant-data#31: the whistleblower's absence no longer blocks a bar from being
        fetched at all -- reconcile.cli decides, using ingestion_coverage, whether a missing
        whistleblower row means "confirmed absent" or "not ingested yet"). expected_provider_names
        (still the full configured set) controls which providers' rows come back for an eligible
        bar -- the whistleblower's row is included when it happens to exist. Also excludes any bar
        with a fact_pending_manual_resolution row, i.e. one that's already exhausted the automatic
        pass and been handed off to --finalize (tasks/quant_reconcile.md's "Updated (2026-08-03)"
        section)."""
        started = time.perf_counter()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.ticker_id, s.date_id, s.time_id, s.timestamp, s.provider_id,
                           s.open, s.high, s.low, s.close, s.volume, s.data_quality
                    FROM staging_market_data_1min s
                    JOIN dim_provider p ON p.provider_id = s.provider_id
                    WHERE p.name = ANY(%(names)s)
                      AND (s.ticker_id, s.date_id, s.time_id) IN (
                          SELECT s2.ticker_id, s2.date_id, s2.time_id
                          FROM staging_market_data_1min s2
                          JOIN dim_provider p2 ON p2.provider_id = s2.provider_id
                          WHERE p2.name = ANY(%(required_names)s)
                          GROUP BY s2.ticker_id, s2.date_id, s2.time_id
                          HAVING COUNT(DISTINCT s2.provider_id) = %(required_count)s
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM fact_pending_manual_resolution fpmr
                          WHERE fpmr.ticker_id = s.ticker_id AND fpmr.date_id = s.date_id AND fpmr.time_id = s.time_id
                      )
                    """,
                    {
                        "names": expected_provider_names,
                        "required_names": required_provider_names,
                        "required_count": len(required_provider_names),
                    },
                )
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch staging rows for reconciliation: {error}") from error
        self._logger.perf(f"fetch_staging_rows_for_reconciliation({len(rows)} rows)", time.perf_counter() - started)

        result: list[StagingRow] = []
        for ticker_id, date_id, time_id, timestamp, provider_id, open_, high, low, close, volume, data_quality in rows:
            result.append(
                StagingRow(
                    ticker_id=ticker_id,
                    date_id=date_id,
                    time_id=time_id,
                    timestamp=timestamp,
                    provider_id=provider_id,
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume),
                    data_quality=data_quality,
                )
            )
        return result

    def fetch_pending_manual_resolution_staging_rows(self) -> list[StagingRow]:
        """Every staging_market_data_1min row belonging to a bar with a fact_pending_manual_resolution
        row -- exactly what --finalize operates on, skipping Tiers 1-3 entirely since they're
        already known to fail for these."""
        started = time.perf_counter()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.ticker_id, s.date_id, s.time_id, s.timestamp, s.provider_id,
                           s.open, s.high, s.low, s.close, s.volume, s.data_quality
                    FROM staging_market_data_1min s
                    WHERE EXISTS (
                        SELECT 1 FROM fact_pending_manual_resolution fpmr
                        WHERE fpmr.ticker_id = s.ticker_id AND fpmr.date_id = s.date_id AND fpmr.time_id = s.time_id
                    )
                    """
                )
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch pending-manual-resolution staging rows: {error}") from error
        self._logger.perf(f"fetch_pending_manual_resolution_staging_rows({len(rows)} rows)", time.perf_counter() - started)

        result: list[StagingRow] = []
        for ticker_id, date_id, time_id, timestamp, provider_id, open_, high, low, close, volume, data_quality in rows:
            result.append(
                StagingRow(
                    ticker_id=ticker_id,
                    date_id=date_id,
                    time_id=time_id,
                    timestamp=timestamp,
                    provider_id=provider_id,
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume),
                    data_quality=data_quality,
                )
            )
        return result

    def fetch_pending_manual_resolution_keys(self) -> set[tuple[int, int, int, int]]:
        """(ticker_id, date_id, time_id, field_group_id) for every (bar, field group) currently
        awaiting --finalize -- lets the caller know exactly which groups to force-resolve, since
        fetch_pending_manual_resolution_staging_rows only returns the raw bar data, not which
        specific field group(s) are pending for it."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT ticker_id, date_id, time_id, field_group_id FROM fact_pending_manual_resolution")
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch pending manual resolution keys: {error}") from error

        result: set[tuple[int, int, int, int]] = set()
        for ticker_id, date_id, time_id, field_group_id in rows:
            result.add((ticker_id, date_id, time_id, field_group_id))
        return result

    def mark_pending_manual_resolution(self, ticker_id: int, date_id: int, time_id: int, field_group_id: int, timestamp: datetime) -> None:
        """Records that a (bar, field group) exhausted the automatic pass's Tiers 1-3 within a run
        and is now awaiting --finalize or manual correction -- future plain quant-reconcile runs
        will skip it entirely via fetch_staging_rows_for_reconciliation's NOT EXISTS check."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO fact_pending_manual_resolution (ticker_id, date_id, time_id, field_group_id, timestamp)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ticker_id, date_id, time_id, field_group_id) DO NOTHING
                    """,
                    (ticker_id, date_id, time_id, field_group_id, timestamp),
                )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to mark ({ticker_id}, {date_id}, {time_id}, group {field_group_id}) pending manual resolution: {error}") from error

    def clear_pending_manual_resolution(self, ticker_id: int, date_id: int, time_id: int, field_group_id: int) -> None:
        """Removes a (bar, field group)'s pending-manual-resolution record once --finalize (or a
        person's manual correction) has actually resolved it."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM fact_pending_manual_resolution WHERE ticker_id = %s AND date_id = %s AND time_id = %s AND field_group_id = %s",
                    (ticker_id, date_id, time_id, field_group_id),
                )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to clear pending manual resolution for ({ticker_id}, {date_id}, {time_id}, group {field_group_id}): {error}") from error

    def fetch_resolved_field_groups(self) -> dict[tuple[int, int, int, int], int]:
        """(ticker_id, date_id, time_id, field_group_id) -> winning_provider_id, for whatever's
        already in fact_reconciliation, scoped to bars still present in staging_market_data_1min
        -- fact_reconciliation itself keeps growing forever (retention: keep everything), so this
        avoids ever loading the whole table. The winning provider is needed (not just "resolved
        or not") so a bar can still be promoted once its other groups resolve, without
        re-deciding a group that resolved in an earlier run."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ticker_id, date_id, time_id, field_group_id, winning_provider_id
                    FROM fact_reconciliation
                    WHERE (ticker_id, date_id, time_id) IN (
                        SELECT DISTINCT ticker_id, date_id, time_id FROM staging_market_data_1min
                    )
                    """
                )
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch resolved field groups: {error}") from error

        result: dict[tuple[int, int, int, int], int] = {}
        for ticker_id, date_id, time_id, field_group_id, winning_provider_id in rows:
            result[(ticker_id, date_id, time_id, field_group_id)] = winning_provider_id
        return result

    def record_reconciliation(
        self,
        ticker_id: int,
        date_id: int,
        time_id: int,
        field_group_id: int,
        winning_provider_id: int,
        resolution_path: str,
        participant_results: list[tuple[int, bool]],
    ) -> None:
        """INSERT fact_reconciliation + one fact_reconciliation_participant row per
        (provider_id, won) in participant_results -- every provider that wrote a staging row for
        this bar, including the whistleblower, per tasks/quant-reconcile.md."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO fact_reconciliation
                        (ticker_id, date_id, time_id, field_group_id, winning_provider_id, resolution_path)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (ticker_id, date_id, time_id, field_group_id, winning_provider_id, resolution_path),
                )
                for provider_id, won in participant_results:
                    cursor.execute(
                        """
                        INSERT INTO fact_reconciliation_participant
                            (ticker_id, date_id, time_id, field_group_id, provider_id, won)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (ticker_id, date_id, time_id, field_group_id, provider_id, won),
                    )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to record reconciliation for ({ticker_id}, {date_id}, {time_id}, group {field_group_id}): {error}") from error

    def promote_bar_to_fact(
        self,
        ticker_id: int,
        date_id: int,
        time_id: int,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        data_quality: str,
    ) -> None:
        """Upsert the fully-resolved bar into fact_market_data_1min -- called once every field
        group for this bar has resolved. Does NOT purge staging rows; that's the separate,
        deliberately lazy purge_staging_bar (see its docstring for why)."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO fact_market_data_1min
                        (ticker_id, date_id, time_id, open, high, low, close, volume, timestamp, data_quality)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker_id, date_id, time_id) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        timestamp = EXCLUDED.timestamp,
                        data_quality = EXCLUDED.data_quality
                    """,
                    (ticker_id, date_id, time_id, open, high, low, close, volume, timestamp, data_quality),
                )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to promote bar ({ticker_id}, {date_id}, {time_id}) to fact_market_data_1min: {error}") from error

    def purge_staging_bar(self, ticker_id: int, date_id: int, time_id: int) -> None:
        """Archives, then deletes, a fully-resolved bar's staging rows for every candidate
        provider -- one transaction, so a row is never deleted without first landing in
        market_data_archive (croicu/quant-data#35, tasks/staging_archive_before_purge.md). Split
        out from promote_bar_to_fact so a bar can be promoted immediately while its raw staging
        data is deliberately kept a little longer if an adjacent bar (t-1/t+1) hasn't resolved yet
        and might still need it as a Tier-3 windowed-average neighbor -- purging immediately on
        promotion permanently lost that data for any future run (tasks/quant_reconcile.md's
        "missing neighbor" gap).

        Each archived row's new archive_id is also written back onto that provider's
        fact_reconciliation_participant row (left NULL by record_reconciliation, since archiving
        always happens later than that INSERT, if it happens at all this run).

        Whistleblower-role providers (yfinance today) are permanently exempt -- their rows are
        never archived or deleted here, unlike candidates' (croicu/quant-data#28). Rationale is
        irreplaceability, not just audit trail: Yahoo's historical access policy is external and
        not guaranteed, so whatever's already been fetched may be the only copy that will ever
        exist. Accepted tradeoff: unbounded storage growth for a provider whose individual bars are
        never promoted. The orphaned whistleblower row becomes permanently inert for reconcile's
        purposes once its candidate siblings are gone (fetch_staging_rows_for_reconciliation's
        expected-provider-count check can never be satisfied again for that bar_key), which needs
        no compensating logic."""
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT provider_id, timestamp, open, high, low, close, volume, data_quality
                    FROM staging_market_data_1min
                    WHERE ticker_id = %s AND date_id = %s AND time_id = %s
                      AND provider_id NOT IN (SELECT provider_id FROM dim_provider WHERE role = %s)
                    """,
                    (ticker_id, date_id, time_id, ProviderRole.WHISTLEBLOWER.value),
                )
                rows_to_archive = cursor.fetchall()

                for provider_id, timestamp, open_, high, low, close, volume, data_quality in rows_to_archive:
                    cursor.execute(
                        """
                        INSERT INTO market_data_archive
                            (provider_id, ticker_id, date_id, time_id, timestamp, open, high, low, close, volume, data_quality)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING archive_id
                        """,
                        (provider_id, ticker_id, date_id, time_id, timestamp, open_, high, low, close, volume, data_quality),
                    )
                    archive_id = cursor.fetchone()[0]
                    cursor.execute(
                        """
                        UPDATE fact_reconciliation_participant
                        SET archive_id = %s
                        WHERE ticker_id = %s AND date_id = %s AND time_id = %s AND provider_id = %s
                        """,
                        (archive_id, ticker_id, date_id, time_id, provider_id),
                    )

                cursor.execute(
                    """
                    DELETE FROM staging_market_data_1min
                    WHERE ticker_id = %s AND date_id = %s AND time_id = %s
                      AND provider_id NOT IN (SELECT provider_id FROM dim_provider WHERE role = %s)
                    """,
                    (ticker_id, date_id, time_id, ProviderRole.WHISTLEBLOWER.value),
                )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to archive and purge staging rows for bar ({ticker_id}, {date_id}, {time_id}): {error}") from error

    def save_provider_pair_disagreement_batch(self, rows: list[tuple[int, int, int, int, float, float, float]]) -> None:
        """Upserts every (provider_id, ticker_id, field_id, sample_count, running_mean, running_m2,
        stddev) row in one transaction -- one commit per call regardless of row count, instead of
        one commit per row. Previously a resolved bar's whole field_group (4 OHLC fields) did 4
        individual round-trip commits; profiling a full reconcile run found that redundant
        3-out-of-4 commit overhead was the dominant cost (croicu/quant-data#30)."""
        if not rows:
            return
        try:
            with self._connection.cursor() as cursor:
                for provider_id, ticker_id, field_id, sample_count, running_mean, running_m2, stddev in rows:
                    cursor.execute(
                        """
                        INSERT INTO provider_pair_disagreement
                            (provider_id, ticker_id, field_id, sample_count, running_mean, running_m2, stddev, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (provider_id, ticker_id, field_id) DO UPDATE SET
                            sample_count = EXCLUDED.sample_count,
                            running_mean = EXCLUDED.running_mean,
                            running_m2 = EXCLUDED.running_m2,
                            stddev = EXCLUDED.stddev,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (provider_id, ticker_id, field_id, sample_count, running_mean, running_m2, stddev),
                    )
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise AppError(f"Failed to save provider_pair_disagreement batch ({len(rows)} row(s)): {error}") from error
