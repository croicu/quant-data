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
