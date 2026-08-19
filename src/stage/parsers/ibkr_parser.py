from __future__ import annotations

from datetime import datetime

from quant_data.protocols import OHLCV, DataQuality


def parse(payload: dict, ticker: str) -> list[OHLCV]:
    """Turn an IBKR-archived payload (raw per-bar dicts, PayloadKind.PARSED_BARS) back into OHLCV
    bars. IBKR only returns bars it actually has trade data for -- no synthetic/NaN placeholder
    rows -- so every bar is ACCEPTED, matching the provider class's behavior before the split
    (croicu/quant-data#56)."""
    normalized_ticker = ticker.upper()
    bars: list[OHLCV] = []
    for raw_bar in payload["bars"]:
        bars.append(
            OHLCV(
                ticker=normalized_ticker,
                timestamp=datetime.fromisoformat(raw_bar["timestamp"]),
                open=float(raw_bar["open"]),
                high=float(raw_bar["high"]),
                low=float(raw_bar["low"]),
                close=float(raw_bar["close"]),
                volume=int(raw_bar["volume"]),
                data_quality=DataQuality.ACCEPTED,
            )
        )
    return bars


def parse_bid_ask(payload: dict) -> dict[datetime, tuple[float, float]]:
    """Turn an IBKR BID_ASK-archived payload into a per-minute (avg_bid, avg_ask) lookup, keyed by
    timestamp -- merged into the primary TRADES-derived OHLCV bars by stage/cli.py's _stage_one
    rather than written as its own staging row (croicu/quant-data#61, quote group: not
    winner-gated). BID_ASK's own archived `high`/`low` fields are deliberately left unconsumed
    here -- unconfirmed semantics, not requested by #61's own scope."""
    result: dict[datetime, tuple[float, float]] = {}
    for raw_bar in payload["bars"]:
        timestamp = datetime.fromisoformat(raw_bar["timestamp"])
        result[timestamp] = (float(raw_bar["avg_bid"]), float(raw_bar["avg_ask"]))
    return result


def parse_midpoint(payload: dict) -> dict[datetime, tuple[float, float, float, float]]:
    """Turn an IBKR MIDPOINT-archived payload into a per-minute (open, high, low, close) lookup of
    the bid/ask midpoint price series, keyed by timestamp -- same merge treatment as
    parse_bid_ask above (croicu/quant-data#61, quote group: not winner-gated)."""
    result: dict[datetime, tuple[float, float, float, float]] = {}
    for raw_bar in payload["bars"]:
        timestamp = datetime.fromisoformat(raw_bar["timestamp"])
        result[timestamp] = (
            float(raw_bar["open"]),
            float(raw_bar["high"]),
            float(raw_bar["low"]),
            float(raw_bar["close"]),
        )
    return result
