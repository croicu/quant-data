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
        raw_wap = raw_bar.get("vw")
        raw_trade_count = raw_bar.get("n")
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
                # Trade group (croicu/quant-data#61) -- free on this same aggregates response, no
                # extra archive read needed. None if a given bar's response happens to omit them.
                wap=None if raw_wap is None else float(raw_wap),
                trade_count=None if raw_trade_count is None else int(raw_trade_count),
            )
        )
    bars.sort(key=_bar_timestamp)
    return bars


def _bar_timestamp(bar: OHLCV) -> datetime:
    return bar.timestamp
