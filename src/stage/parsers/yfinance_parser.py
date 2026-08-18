from __future__ import annotations

from datetime import datetime

from quant_data.protocols import OHLCV, DataQuality


def parse(payload: dict, ticker: str) -> list[OHLCV]:
    """Turn a yfinance-archived payload (raw per-bar dicts, PayloadKind.PARSED_BARS) back into
    OHLCV bars -- the data-quality determination moved here from the provider class itself
    (croicu/quant-data#56): a raw field of None (yfinance's own NaN, preserved as JSON null rather
    than coerced at fetch time) or a real zero volume both mean 'incomplete', exactly as before
    the split."""
    normalized_ticker = ticker.upper()
    bars: list[OHLCV] = []
    for raw_bar in payload["bars"]:
        open_value, open_incomplete = _value_or_zero(raw_bar["open"])
        high_value, high_incomplete = _value_or_zero(raw_bar["high"])
        low_value, low_incomplete = _value_or_zero(raw_bar["low"])
        close_value, close_incomplete = _value_or_zero(raw_bar["close"])
        volume_raw = raw_bar["volume"]
        volume_incomplete = volume_raw is None or volume_raw == 0
        volume = 0 if volume_raw is None else int(volume_raw)

        incomplete = open_incomplete or high_incomplete or low_incomplete or close_incomplete or volume_incomplete

        bars.append(
            OHLCV(
                ticker=normalized_ticker,
                timestamp=datetime.fromisoformat(raw_bar["timestamp"]),
                open=open_value,
                high=high_value,
                low=low_value,
                close=close_value,
                volume=volume,
                data_quality=DataQuality.INCOMPLETE if incomplete else DataQuality.ACCEPTED,
            )
        )
    return bars


def _value_or_zero(value: float | None) -> tuple[float, bool]:
    if value is None:
        return 0.0, True
    return float(value), False
