from __future__ import annotations

from datetime import date
from types import TracebackType

from quant_data.protocols import OHLCV
from quant_data_internal.contracts import MarketDataProvider


class MarketData:
    """Thin, read-only wrapper around a MarketDataProvider for external consumers (e.g. quant-scratch).

    Agnostic of the concrete backend (Postgres today, possibly a cloud store later) -- it only
    knows the MarketDataProvider protocol. Use a factory (e.g. create_postgres_provider) to build
    the provider to pass in. Context-manageable -- `with MarketData(provider) as client:` closes
    the provider on exit, same as calling close() manually.

    Deliberately doesn't expose write_bars at all. For the Postgres backend, the real enforcement
    of "no client can write" is the quant_reader role's database privileges, not this class's
    shape (see docs/ARCHITECTURE.md) -- but a narrower surface is still better ergonomics than
    handing consumers the full read/write PostgresDatabase ingest itself uses.
    """

    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        return self._provider.fetch_bars(ticker, start_date, end_date)

    def close(self) -> None:
        self._provider.close()

    def __enter__(self) -> MarketData:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()
