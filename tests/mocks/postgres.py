from __future__ import annotations

from datetime import date

from quant_data.protocols import OHLCV, PendingResolutionBar


class MockPostgresDatabase:
    def __init__(self) -> None:
        self.written_bars: list[OHLCV] = []
        self.written_staging_bars: list[tuple[str, OHLCV]] = []
        self.pending_resolution_bars: list[PendingResolutionBar] = []
        self.closed = False

    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        normalized_ticker = ticker.upper()

        matches: list[OHLCV] = []
        for bar in self.written_bars:
            if bar.ticker == normalized_ticker and start_date <= bar.timestamp.date() <= end_date:
                matches.append(bar)

        return matches

    def fetch_pending_resolution_bars(self, ticker: str, start_date: date, end_date: date) -> list[PendingResolutionBar]:
        normalized_ticker = ticker.upper()

        matches: list[PendingResolutionBar] = []
        for candidate in self.pending_resolution_bars:
            if candidate.bar.ticker == normalized_ticker and start_date <= candidate.bar.timestamp.date() <= end_date:
                matches.append(candidate)

        return matches

    def write_bars(self, bars: list[OHLCV]) -> int:
        for bar in bars:
            self.written_bars.append(bar)

        return len(bars)

    def write_staging_bars(self, provider_name: str, bars: list[OHLCV]) -> int:
        for bar in bars:
            self.written_staging_bars.append((provider_name, bar))

        return len(bars)

    def close(self) -> None:
        self.closed = True
