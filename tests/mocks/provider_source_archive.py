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


class MockProviderSourceArchiveReader:
    """Fake ProviderSourceArchiveReader, seeded with archived (ticker, provider, trading_date) ->
    (payload_kind, payload) entries -- for testing stage/cli.py without a real quant_ingest
    connection."""

    def __init__(self, archived: dict[tuple[str, str, date], tuple[PayloadKind, dict]] | None = None) -> None:
        self._archived = archived if archived is not None else {}
        self.closed = False

    def fetch_latest_bars(self, ticker: str, provider: str, trading_date: date) -> tuple[PayloadKind, dict] | None:
        return self._archived.get((ticker.upper(), provider.lower(), trading_date))

    def close(self) -> None:
        self.closed = True
