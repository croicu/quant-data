"""Public API surface for quant_data.

External consumers should only import from this top-level package
(``from quant_data import MarketData, OHLCV, create_postgres_provider``). ``MarketData`` is
agnostic of the concrete backend -- it only depends on the MarketDataProvider protocol; use a
factory (e.g. ``create_postgres_provider``) to build the provider passed to it. ``quant_data_internal``
(shared infra + behavioral Protocols) and ``ingest`` (the write-side CLI, no importable surface at
all — only the ``quant-ingest`` console script) are separate top-level packages, not nested under
``quant_data``, but neither is meant to be imported directly by external consumers.
"""

from __future__ import annotations

__all__ = ["MarketData", "OHLCV", "create_postgres_provider"]


def __getattr__(name: str):
    # Lazy on purpose: quant_data_internal.shared.postgres needs OHLCV from quant_data.protocols,
    # and MarketData (below) needs a provider built from quant_data_internal.shared.postgres in
    # turn. Importing these eagerly here would make that a real circular import whenever something
    # touches quant_data_internal before quant_data itself; deferring the import until the
    # attribute is actually accessed breaks the cycle.
    if name == "MarketData":
        from quant_data.client.market_data import MarketData

        return MarketData
    if name == "OHLCV":
        from quant_data.protocols import OHLCV

        return OHLCV
    if name == "create_postgres_provider":
        from quant_data.client.postgres_provider import create_postgres_provider

        return create_postgres_provider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
