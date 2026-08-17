from __future__ import annotations

from datetime import date

from quant_data._internal.shared.errors import AppError
from quant_data.protocols import OHLCV, PendingResolutionBar, RejectedWhistleblowerBar


class MockPostgresDatabase:
    def __init__(
        self,
        inception_date: date | None = None,
        earliest_covered_by_ticker: dict[str, date] | None = None,
    ) -> None:
        self.written_bars: list[OHLCV] = []
        self.written_staging_bars: list[tuple[str, OHLCV]] = []
        self.recorded_coverage: list[tuple[str, str, date]] = []
        self.pending_resolution_bars: list[PendingResolutionBar] = []
        self.rejected_whistleblower_bars: list[RejectedWhistleblowerBar] = []
        self.inception_date = inception_date
        self.earliest_covered_by_ticker = earliest_covered_by_ticker if earliest_covered_by_ticker is not None else {}
        self.closed = False

    def fetch_dataset_inception_date(self) -> date:
        if self.inception_date is None:
            raise AppError("dataset_inception is empty -- insert the dataset's actual inception_date before running --backfill.")
        return self.inception_date

    def fetch_earliest_covered_date(self, ticker: str) -> date | None:
        return self.earliest_covered_by_ticker.get(ticker.upper())

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

    def fetch_rejected_whistleblower_bars(self, ticker: str, start_date: date, end_date: date) -> list[RejectedWhistleblowerBar]:
        normalized_ticker = ticker.upper()

        matches: list[RejectedWhistleblowerBar] = []
        for rejected in self.rejected_whistleblower_bars:
            if rejected.bar.ticker == normalized_ticker and start_date <= rejected.bar.timestamp.date() <= end_date:
                matches.append(rejected)

        return matches

    def write_bars(self, bars: list[OHLCV]) -> int:
        for bar in bars:
            self.written_bars.append(bar)

        return len(bars)

    def write_staging_bars(self, provider_name: str, bars: list[OHLCV]) -> int:
        for bar in bars:
            self.written_staging_bars.append((provider_name, bar))

        return len(bars)

    def record_ingestion_coverage(self, provider_name: str, ticker: str, target_date: date) -> None:
        self.recorded_coverage.append((provider_name.lower(), ticker.upper(), target_date))

    def close(self) -> None:
        self.closed = True
