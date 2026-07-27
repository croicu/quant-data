"""Runtime behavioral interfaces.

`typing.Protocol` classes describing behavior (e.g. workers, executors) — not data. Persisted
or shared data contracts belong in protocols.py instead.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from quant_data.protocols import OHLCV


class MarketDataProvider(Protocol):
    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        """Read bars from the warehouse for a ticker over an inclusive date range. Read-only —
        no write methods on this contract; see quant_data_internal.shared.postgres.PostgresDatabase
        for the concrete implementation, which does expose a write path but only for the
        ingest-side caller."""
        ...

    def close(self) -> None:
        """Release any resources (e.g. a database connection) held by this provider."""
        ...


class IntraDayProvider(Protocol):
    def fetch_bars(self, ticker: str, target_date: date) -> list[OHLCV]:
        """Fetch 1-minute OHLCV bars for a single session day from an external data provider.
        Raises AppError if the ticker is invalid, the network call fails, or no bars are
        available for that date."""
        ...
