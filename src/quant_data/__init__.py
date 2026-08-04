"""Public API surface for quant_data.

External consumers should only import from this top-level package
(``from quant_data import MarketData, OHLCV, create_postgres_provider``). ``MarketData`` is
agnostic of the concrete backend -- it only depends on the MarketDataProvider protocol; use a
factory (e.g. ``create_postgres_provider``) to build the provider passed to it. ``quant_data._internal``
(shared infra + behavioral Protocols) is nested private implementation; ``ingest`` (the write-side
CLI, no importable surface at all — only the ``quant-ingest`` console script) is a separate
top-level package. Neither is meant to be imported directly by external consumers.
"""

from __future__ import annotations

from quant_data.client.market_data import MarketData
from quant_data.client.postgres_provider import create_postgres_provider
from quant_data.protocols import OHLCV, LoggingSink, PendingResolutionBar

__all__ = ["MarketData", "OHLCV", "LoggingSink", "PendingResolutionBar", "create_postgres_provider"]
