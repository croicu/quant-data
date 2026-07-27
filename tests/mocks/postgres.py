from __future__ import annotations

from datetime import date

from quant_data.protocols import OHLCV


class MockPostgresDatabase:
    def __init__(self) -> None:
        self.written_bars: list[OHLCV] = []
        self.closed = False

    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        normalized_ticker = ticker.upper()

        matches: list[OHLCV] = []
        for bar in self.written_bars:
            if bar.ticker == normalized_ticker and start_date <= bar.timestamp.date() <= end_date:
                matches.append(bar)

        return matches

    def write_bars(self, bars: list[OHLCV]) -> int:
        for bar in bars:
            self.written_bars.append(bar)

        return len(bars)

    def close(self) -> None:
        self.closed = True
