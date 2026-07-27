from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from quant_data.protocols import OHLCV
from quant_data_internal.shared.errors import AppError

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "ohlcv_bars.json"


class MockIntraDayProvider:
    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        with data_path.open("r", encoding="utf-8") as f:
            self._bars_by_ticker: dict = json.load(f)

    def fetch_bars(self, ticker: str, target_date: date) -> list[OHLCV]:
        normalized_ticker = ticker.upper()

        ticker_data = self._bars_by_ticker.get(normalized_ticker)
        if ticker_data is None:
            raise AppError(f"No mock intraday data for '{normalized_ticker}'.")

        day_data = ticker_data.get(target_date.isoformat())
        if day_data is None:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        bars: list[OHLCV] = []
        for bar_data in day_data:
            timestamp_utc = datetime.fromisoformat(bar_data["timestamp"])
            bar = OHLCV(
                ticker=normalized_ticker,
                timestamp=timestamp_utc,
                open=float(bar_data["open"]),
                high=float(bar_data["high"]),
                low=float(bar_data["low"]),
                close=float(bar_data["close"]),
                volume=int(bar_data["volume"]),
                incomplete=bool(bar_data.get("incomplete", False)),
            )
            bars.append(bar)

        return bars
