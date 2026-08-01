from __future__ import annotations

import time
from datetime import date

import psycopg

from quant_data._internal.contracts import ConnectionTransport
from quant_data.protocols import OHLCV, LoggingSink

from .diagnostics import Logger
from .errors import AppError, DateOutOfRangeError

CATEGORY_POSTGRES = "postgres"


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
            SELECT t.ticker, f.timestamp, f.open, f.high, f.low, f.close, f.volume, f.incomplete
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
            row_ticker, row_timestamp, row_open, row_high, row_low, row_close, row_volume, row_incomplete = row
            bar = OHLCV(
                ticker=row_ticker,
                timestamp=row_timestamp,
                open=float(row_open),
                high=float(row_high),
                low=float(row_low),
                close=float(row_close),
                volume=int(row_volume),
                incomplete=bool(row_incomplete),
            )
            bars.append(bar)

        return bars

    def write_bars(self, bars: list[OHLCV]) -> int:
        written = 0
        started = time.perf_counter()

        try:
            with self._connection.cursor() as cursor:
                for bar in bars:
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

                    cursor.execute(
                        """
                        INSERT INTO fact_market_data_1min
                            (ticker_id, date_id, time_id, open, high, low, close, volume, timestamp, incomplete)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticker_id, date_id, time_id) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            timestamp = EXCLUDED.timestamp,
                            incomplete = EXCLUDED.incomplete
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
                            bar.incomplete,
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
