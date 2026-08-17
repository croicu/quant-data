"""Runtime behavioral interfaces.

`typing.Protocol` classes describing behavior (e.g. workers, executors) — not data. Persisted
or shared data contracts belong in protocols.py instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol

from quant_data.protocols import OHLCV, PendingResolutionBar, RejectedWhistleblowerBar


class MarketDataProvider(Protocol):
    def fetch_bars(self, ticker: str, start_date: date, end_date: date) -> list[OHLCV]:
        """Read bars from the warehouse for a ticker over an inclusive date range. Read-only —
        no write methods on this contract; see quant_data._internal.shared.postgres.PostgresDatabase
        for the concrete implementation, which does expose a write path but only for the
        ingest-side caller."""
        ...

    def fetch_pending_resolution_bars(self, ticker: str, start_date: date, end_date: date) -> list[PendingResolutionBar]:
        """Read every provider's disputed staging value for (bar, field group)s still awaiting
        manual resolution (fact_pending_manual_resolution), for a ticker over an inclusive date
        range -- one entry per (bar, field group, provider) that reported a staging value, so a
        caller can see the actual disagreement instead of just that a bar is stuck. Read-only,
        same contract shape as fetch_bars."""
        ...

    def fetch_rejected_whistleblower_bars(self, ticker: str, start_date: date, end_date: date) -> list[RejectedWhistleblowerBar]:
        """Read every whistleblower-reported staging value with data_quality=REJECTED, for a
        ticker over an inclusive date range -- distinct from fetch_pending_resolution_bars, since
        a rejected whistleblower value with an accepted candidate auto-resolves via Tier 1 and
        never reaches fact_pending_manual_resolution at all. Read-only, same contract shape as
        fetch_bars."""
        ...

    def close(self) -> None:
        """Release any resources (e.g. a database connection) held by this provider."""
        ...


class PayloadKind(Enum):
    """What ProviderFetchResult.payload actually holds -- not uniform across providers.
    RAW_API_RESPONSE is the literal API response (only genuinely available where this repo talks
    HTTP directly, e.g. Massive); PARSED_BARS is a JSON-serialized form of the parsed OHLCV bars
    for providers with no raw payload this repo's code ever sees (yfinance parses its own HTTP
    call internally; IBKR's wire protocol isn't JSON at all). Mirrors quant_ingest's
    provider_source_archive.payload_kind CHECK constraint (croicu/quant-data#52)."""

    RAW_API_RESPONSE = "raw_api_response"
    PARSED_BARS = "parsed_bars"


@dataclass
class ProviderFetchResult:
    """IntraDayProvider.fetch_bars()'s return value -- the parsed bars ingest actually writes to
    staging, plus whatever's closest to the original provider response, for archiving into
    quant_ingest before anything else (parsing bugs included) can lose it (croicu/quant-data#52).
    Colocated with IntraDayProvider itself as the one data shape this module holds, rather than in
    protocols.py: it's purely internal, never exposed to a consumer implementing a public
    Protocol."""

    bars: list[OHLCV]
    payload: dict
    payload_kind: PayloadKind


class IntraDayProvider(Protocol):
    FETCH_VERSION: str
    """Bumped by hand whenever this provider's own request construction changes (a new param, a
    changed default) in a way that could change what comes back -- lets a later pass identify
    which quant_ingest.archive_coverage ranges were fetched under an outdated query shape.
    Deliberately a plain string, not numeric -- free to be an incrementing counter, a descriptive
    tag, or anything else meaningful when actually bumped."""

    def connect(self) -> None:
        """Establish whatever's needed to fetch (e.g. a persistent API connection), so it can be
        amortized across a batch of fetch_bars() calls rather than reconnecting each time. A
        no-op for stateless/per-call providers (e.g. plain HTTP). Called once per batch, before
        any fetch_bars() calls."""
        ...

    def fetch_bars(self, ticker: str, target_date: date) -> ProviderFetchResult:
        """Fetch 1-minute OHLCV bars for a single session day from an external data provider.
        Raises AppError if the ticker is invalid, the network call fails, or no bars are
        available for that date."""
        ...

    def close(self) -> None:
        """Release any resources opened by connect(). A no-op for stateless providers."""
        ...


class ConnectionTransport(Protocol):
    def open(self) -> tuple[str, int]:
        """Establish whatever's needed to reach Postgres (e.g. an SSH tunnel), returning the
        (host, port) that psycopg should connect to. Hosting-specific — see
        quant_data._internal.shared.transports for the concrete implementations (direct connect
        vs. SSH tunnel), which is what keeps PostgresDatabase itself agnostic of the concrete
        hosting/transport choice."""
        ...

    def close(self) -> None:
        """Release any resources (e.g. a tunnel process) opened by open()."""
        ...
