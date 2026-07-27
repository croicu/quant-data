from __future__ import annotations

from datetime import date

import psycopg

from defs.protocols import OHLCV

from .errors import AppError, DateOutOfRangeError


class PostgresDatabase:
    """Concrete MarketDataProvider implementation, plus a write path used only by ingest.

    Single connection per invocation (no pooling) — one instance is created per CLI run and
    reused for every query/write during that run, then closed when the process exits.
    """

    def __init__(self, host: str, port: int, user: str, password: str, dbname: str) -> None:
        try:
            self._connection = psycopg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
            )
        except psycopg.Error as error:
            raise AppError(f"Failed to connect to Postgres at {host}:{port}/{dbname}: {error}") from error

    def close(self) -> None:
        self._connection.close()

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

        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (normalized_ticker, start_date, end_date))
                rows = cursor.fetchall()
        except psycopg.Error as error:
            raise AppError(f"Failed to fetch bars for '{normalized_ticker}' from {start_date} to {end_date}: {error}") from error

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

        return written
