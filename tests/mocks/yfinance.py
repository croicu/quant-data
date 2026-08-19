from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quant_data._internal.contracts import DEFAULT_METHODS_BY_PROVIDER, PayloadKind, ProviderFetchResult
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.payload import raw_bars_payload

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "ohlcv_bars.json"


class MockIntraDayProvider:
    FETCH_VERSION = "1"
    DEFAULT_METHODS = DEFAULT_METHODS_BY_PROVIDER["yfinance"]

    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        with data_path.open("r", encoding="utf-8") as f:
            self._bars_by_ticker: dict = json.load(f)
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        # Pure fetch, matching the real providers post-split (croicu/quant-data#56) -- returns raw
        # per-bar dicts, no OHLCV construction.
        normalized_ticker = ticker.upper()
        effective_method = method if method is not None else self.DEFAULT_METHODS[0]

        ticker_data = self._bars_by_ticker.get(normalized_ticker)
        if ticker_data is None:
            raise AppError(f"No mock intraday data for '{normalized_ticker}'.")

        day_data = ticker_data.get(target_date.isoformat())
        if day_data is None:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        raw_bars: list[dict] = []
        for bar_data in day_data:
            raw_bars.append(
                {
                    "timestamp": bar_data["timestamp"],
                    "open": bar_data["open"],
                    "high": bar_data["high"],
                    "low": bar_data["low"],
                    "close": bar_data["close"],
                    "volume": bar_data["volume"],
                }
            )

        return ProviderFetchResult(payload=raw_bars_payload(raw_bars), payload_kind=PayloadKind.PARSED_BARS, method=effective_method)
