from __future__ import annotations

from datetime import date

from defs.protocols import OHLCV
from shared.postgres import PostgresDatabase


class MarketData:
    """Thin, read-only wrapper around PostgresDatabase for external consumers (e.g. quant-scratch).

    Deliberately doesn't expose write_bars at all. The real enforcement of "no client can write"
    is the quant_reader role's database privileges, not this class's shape (see
    docs/ARCHITECTURE.md) -- but a narrower surface is still better ergonomics than handing
    consumers the full read/write PostgresDatabase ingest itself uses.
    """

    def __init__(self, *, host: str, port: int, dbname: str, user: str = "quant_reader", password: str = "") -> None:
        self._database = PostgresDatabase(host=host, port=port, user=user, password=password, dbname=dbname)

    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        return self._database.fetch_bars(ticker, start_date, end_date)

    def close(self) -> None:
        self._database.close()
