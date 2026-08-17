from __future__ import annotations

from datetime import date

from quant_data._internal.contracts import PayloadKind


class MockProviderSourceArchiveWriter:
    def __init__(self) -> None:
        self.recorded_fetches: list[tuple[str, str, date, str, PayloadKind, dict]] = []
        self.closed = False

    def record_fetch(
        self,
        ticker: str,
        provider: str,
        trading_date: date,
        fetch_version: str,
        payload_kind: PayloadKind,
        payload: dict,
    ) -> None:
        self.recorded_fetches.append((ticker.upper(), provider.lower(), trading_date, fetch_version, payload_kind, payload))

    def close(self) -> None:
        self.closed = True
