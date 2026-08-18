from __future__ import annotations

from datetime import datetime, timezone

from quant_data._internal.shared.errors import AppError
from quant_data.protocols import OHLCV, DataQuality


def parse(payload: dict, ticker: str) -> list[OHLCV]:
    """Turn a Massive-archived payload (raw API response, PayloadKind.RAW_API_RESPONSE) back into
    OHLCV bars. Massive -- like IBKR -- only returns bars it actually has trade data for, so every
    bar is ACCEPTED, matching the provider class's behavior before the split
    (croicu/quant-data#56). `ticker` is required explicitly, unlike the other parsers -- Massive's
    raw API response carries no per-bar ticker field of its own (it's implied by the request URL,
    which isn't archived)."""
    normalized_ticker = ticker.upper()
    raw_bars = payload.get("results")
    if not raw_bars:
        raise AppError(f"Archived Massive payload for '{normalized_ticker}' has no 'results'.")

    bars: list[OHLCV] = []
    for raw_bar in raw_bars:
        timestamp_utc = datetime.fromtimestamp(raw_bar["t"] / 1000, tz=timezone.utc)
        bars.append(
            OHLCV(
                ticker=normalized_ticker,
                timestamp=timestamp_utc,
                open=float(raw_bar["o"]),
                high=float(raw_bar["h"]),
                low=float(raw_bar["l"]),
                close=float(raw_bar["c"]),
                volume=int(raw_bar["v"]),
                data_quality=DataQuality.ACCEPTED,
            )
        )
    bars.sort(key=_bar_timestamp)
    return bars


def _bar_timestamp(bar: OHLCV) -> datetime:
    return bar.timestamp
