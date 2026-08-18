from __future__ import annotations

from quant_data._internal.shared.errors import AppError
from quant_data.protocols import OHLCV

from . import ibkr_parser, massive_parser, yfinance_parser

_PARSERS = {
    "yfinance": yfinance_parser.parse,
    "ibkr": ibkr_parser.parse,
    "massive": massive_parser.parse,
}


def parse_payload(provider: str, payload: dict, ticker: str) -> list[OHLCV]:
    """Dispatch an archived provider_source_archive payload to its provider-specific parser.
    Raises AppError for a provider with no registered parser (a new IntraDayProvider was added to
    `ingest` without a matching `stage` parser)."""
    parser = _PARSERS.get(provider)
    if parser is None:
        raise AppError(f"No stage parser registered for provider '{provider}'.")
    return parser(payload, ticker)
