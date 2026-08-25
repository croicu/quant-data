"""Read-only warehouse access for tasks/ibkr_massive_mad_calibration.md's experiments.

Invariant 1 (read-only against the warehouse): always connects as `quant_reader`, ignoring
whatever settings.json/settings.local.json's own `postgres.user` is set to (on this box that's
`alex`, a superuser, never appropriate for this code) -- `quant_reader`'s SELECT-only grant is
enforced at the Postgres privilege level, so any accidental write attempt from this module would
fail with a real `permission denied`, not just a missing method. Connection setup deliberately
duplicates the small amount of logic in `quant_data._internal.shared.postgres.PostgresDatabase.
__init__` (transport open, `localhost` normalization, `TimeZone=UTC` pin) rather than reaching
into that class's private `_connection` attribute -- this module only ever needs read access, not
`PostgresDatabase`'s write-side methods.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pandas as pd
import psycopg

from quant_data._internal.shared.settings import Settings
from quant_data._internal.shared.transports import resolve_transport

READ_ONLY_USER = "quant_reader"
READ_ONLY_PASSWORD = ""

STAGING_COLUMNS = ["timestamp", "provider", "open", "high", "low", "close", "volume", "data_quality"]


@contextmanager
def connect_read_only(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    resolved_settings = settings if settings is not None else Settings.load()
    if resolved_settings.postgres is None:
        raise RuntimeError("settings.json/settings.local.json has no 'postgres' section configured.")

    transport = resolve_transport(
        host=resolved_settings.postgres.host,
        port=resolved_settings.postgres.port,
        ssh_user=resolved_settings.postgres.ssh_user,
        ssh_key_path=resolved_settings.postgres.ssh_key_path,
    )
    effective_host, effective_port = transport.open()
    if effective_host == "localhost":
        # Same dual-stack loopback trap PostgresDatabase.__init__ guards against -- see
        # quant-data#19.
        effective_host = "127.0.0.1"

    connection = psycopg.connect(
        host=effective_host,
        port=effective_port,
        user=READ_ONLY_USER,
        password=READ_ONLY_PASSWORD,
        dbname=resolved_settings.postgres.dbname,
        options="-c TimeZone=UTC",
    )
    try:
        yield connection
    finally:
        connection.close()
        transport.close()


def fetch_staging_rows(connection: psycopg.Connection, ticker: str, providers: tuple[str, ...], start_date: date, end_date: date) -> pd.DataFrame:
    """Every staging_market_data_1min row for `ticker`/`providers` whose UTC timestamp falls
    within `start_date`/`end_date`'s ET calendar range. Padded by a day on each side in the query
    itself, since a bar's UTC timestamp can roll past midnight relative to its ET calendar date
    (extended-hours bars up to 20:00 ET land at or after 00:00 UTC the next day) -- callers should
    still filter/derive the ET date themselves from the returned `timestamp` column rather than
    trusting this function's bounds as exact.

    Read-only: this is the only query this module issues, and it is a plain SELECT.
    """
    padded_start = datetime.combine(start_date - timedelta(days=1), datetime.min.time())
    padded_end = datetime.combine(end_date + timedelta(days=2), datetime.min.time())

    query = """
        SELECT s.timestamp, p.name AS provider, s.open, s.high, s.low, s.close, s.volume, s.data_quality
        FROM staging_market_data_1min s
        JOIN dim_ticker t ON t.ticker_id = s.ticker_id
        JOIN dim_provider p ON p.provider_id = s.provider_id
        WHERE t.ticker = %s
          AND p.name = ANY(%s)
          AND s.timestamp >= %s
          AND s.timestamp < %s
        ORDER BY s.timestamp, p.name
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (ticker.upper(), list(providers), padded_start, padded_end))
        rows = cursor.fetchall()

    return pd.DataFrame(rows, columns=STAGING_COLUMNS)
