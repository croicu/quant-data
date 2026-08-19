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


DEFAULT_METHODS_BY_PROVIDER: dict[str, list[str]] = {
    "yfinance": ["history"],
    "ibkr": ["TRADES", "BID_ASK", "MIDPOINT"],
    "massive": ["aggregates"],
}
"""Single source of truth for which `method`(s) `ingest` archives for a provider when the caller
doesn't ask for a specific one (croicu/quant-data#60, tasks/ingestion_layer_spec.md Sec 2.2) --
consumed by each IntraDayProvider's own `DEFAULT_METHODS` class attribute and by `ingest/cli.py`
to resolve which methods to loop over for a provider with no settings override (only IBKR's
settings currently expose one, settings.ibkr.methods, since it's the only provider genuinely
multi-valued today). Massive and yfinance are single-valued -- `["aggregates"]`/`["history"]`
just name their one real call. IBKR's default set is `["TRADES", "BID_ASK", "MIDPOINT"]` --
`MIDPOINT` added 2026-08-19 on an explicit collect-now-decide-later basis (repo owner's call):
unlike `BID_ASK`'s flat per-bar averages, `MIDPOINT` is a genuine OHLC series of the bid/ask
midpoint price, not reconstructable from `BID_ASK` alone, and `provider_source_archive` already
has a real `DELETE` grant for exactly this kind of "collect it, drop it later if it turns out
unused" cleanup. `ADJUSTED_LAST` (tasks/ingestion_variable_inventory.md Sec 1.5) remains excluded
-- add it here (and to settings.ibkr.methods' allowed values) once there's a concrete reason."""

PRIMARY_METHOD_BY_PROVIDER: dict[str, str] = {}
for _provider_name, _default_methods in DEFAULT_METHODS_BY_PROVIDER.items():
    PRIMARY_METHOD_BY_PROVIDER[_provider_name] = _default_methods[0]
"""The single method `stage` consumes per provider for OHLCV staging -- always each provider's
own first default method (index 0 of DEFAULT_METHODS_BY_PROVIDER above), since `stage`'s parsers
only know how to turn an OHLCV-shaped payload into staging_market_data_1min rows today. `stage`
has no provider objects of its own, only provider name strings from settings.providers, so it
reads this dict directly rather than going through any IntraDayProvider instance. A provider
archiving additional methods (e.g. IBKR's BID_ASK) doesn't change what `stage` consumes -- those
extra archived rows just sit in provider_source_archive, unconsumed, until a BID_ASK-aware parser
is actually built (an open question, not yet scoped)."""


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
    """IntraDayProvider.fetch_bars()'s return value -- whatever's closest to the original provider
    response, for archiving into quant_ingest (croicu/quant-data#52). Providers are pure fetchers
    (croicu/quant-data#56): no OHLCV parsing happens here -- that's the `stage` process's job,
    reading this same payload back out of quant_ingest.provider_source_archive. Colocated with
    IntraDayProvider itself as the one data shape this module holds, rather than in protocols.py:
    it's purely internal, never exposed to a consumer implementing a public Protocol.

    `method` (croicu/quant-data#60) echoes back which method this particular result was fetched
    for -- self-describing, so a caller looping over several methods doesn't have to separately
    track which request produced which result, and a provider that silently fetched something
    other than what was asked would be caught by the caller rather than mislabeled on archive."""

    payload: dict
    payload_kind: PayloadKind
    method: str


class IntraDayProvider(Protocol):
    FETCH_VERSION: str
    """Bumped by hand whenever this provider's own request construction changes (a new param, a
    changed default) in a way that could change what comes back -- lets a later pass identify
    which quant_ingest.archive_coverage ranges were fetched under an outdated query shape.
    Deliberately a plain string, not numeric -- free to be an incrementing counter, a descriptive
    tag, or anything else meaningful when actually bumped."""

    DEFAULT_METHODS: list[str]
    """This provider's own entry in DEFAULT_METHODS_BY_PROVIDER above -- which method(s)
    fetch_bars() archives under when the caller doesn't ask for one specifically. First-class key
    component of provider_source_archive (croicu/quant-data#60), not incidental metadata: a
    provider's blob is not self-describing (e.g. IBKR's serialized BarData is ambiguous on replay
    without knowing whether it came from TRADES vs BID_ASK vs MIDPOINT)."""

    def connect(self) -> None:
        """Establish whatever's needed to fetch (e.g. a persistent API connection), so it can be
        amortized across a batch of fetch_bars() calls rather than reconnecting each time. A
        no-op for stateless/per-call providers (e.g. plain HTTP). Called once per batch, before
        any fetch_bars() calls."""
        ...

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        """Fetch 1-minute bars for a single session day and a single method from an external data
        provider, returned as an archivable payload -- no OHLCV parsing (croicu/quant-data#56; see
        `stage` for that). `method=None` means this provider's own primary method
        (DEFAULT_METHODS[0]) -- callers wanting a provider's full default set (croicu/quant-data#60)
        loop over DEFAULT_METHODS themselves and call this once per method, rather than this method
        fetching multiple methods in one call; that keeps rate limiting (which sits between the
        orchestration loop and this call, not inside any provider) accurate per real API request.
        Raises AppError if the ticker is invalid, the method isn't one this provider recognizes, the
        network call fails, or no bars are available for that date."""
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
