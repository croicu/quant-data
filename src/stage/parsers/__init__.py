from __future__ import annotations

from datetime import datetime

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


def apply_supplementary_payload(provider: str, method: str, payload: dict, bars_by_timestamp: dict[datetime, OHLCV]) -> None:
    """Merge an archived supplementary-method payload (croicu/quant-data#61's quote-group fields,
    not covered by parse_payload's primary OHLCV parse) into the matching primary bars, keyed by
    timestamp -- mutates the OHLCV objects in bars_by_timestamp in place. A timestamp present in
    the supplementary payload but missing from bars_by_timestamp is skipped (no matching primary
    bar to attach to). No-op for any (provider, method) with no registered supplementary parser --
    not an error, since a provider with only a primary method (e.g. Massive, yfinance) has none by
    design."""
    if provider == "ibkr" and method == "BID_ASK":
        bid_ask_by_timestamp = ibkr_parser.parse_bid_ask(payload)
        for timestamp, values in bid_ask_by_timestamp.items():
            bar = bars_by_timestamp.get(timestamp)
            if bar is None:
                continue
            avg_bid, avg_ask = values
            bar.avg_bid = avg_bid
            bar.avg_ask = avg_ask
        return

    if provider == "ibkr" and method == "MIDPOINT":
        midpoint_by_timestamp = ibkr_parser.parse_midpoint(payload)
        for timestamp, values in midpoint_by_timestamp.items():
            bar = bars_by_timestamp.get(timestamp)
            if bar is None:
                continue
            midpoint_open, midpoint_high, midpoint_low, midpoint_close = values
            bar.midpoint_open = midpoint_open
            bar.midpoint_high = midpoint_high
            bar.midpoint_low = midpoint_low
            bar.midpoint_close = midpoint_close
        return
