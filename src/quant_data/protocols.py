"""Public contracts: persisted/shared data (dataclasses) and behavioral Protocols meant for a
consumer to actually implement/inject (as opposed to _internal/contracts.py's Protocols, which
wire quant_data's own internals together and are never imported by consumers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class DataQuality(Enum):
    """Mirrors staging_market_data_1min/fact_market_data_1min's data_quality CHECK constraint --
    a closed set, same precedent as ProviderRole below. Replaces the old plain incomplete: bool
    field: ACCEPTED is the old False, INCOMPLETE is the old True (no confidence in the value, but
    no positive evidence it's wrong -- a real zero-volume bar, or a bar a plausibility check
    couldn't run against), and REJECTED is new (a per-provider staging quality check ran and found
    the value implausible -- see tasks/yahoo_data_sanitization.md). REJECTED is treated
    identically to INCOMPLETE by reconcile's Tier 1 completeness check -- the distinction is for
    audit/debugging, not different promotion behavior."""

    ACCEPTED = "accepted"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"


@dataclass
class OHLCV:
    ticker: str
    timestamp: datetime  # timezone-aware, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int
    data_quality: DataQuality = DataQuality.ACCEPTED
    # Supplement fields (croicu/quant-data#61) -- all optional, default None: not every provider
    # reports them, and fact_market_data_1min's own promotion rule leaves them null even when a
    # provider did report them but didn't win that bar's OHLC vote (trade group only -- see the
    # module-level split below). Purely additive to this constructor, not a breaking change.
    #
    # Trade group -- computed from the same trade prints as OHLC/volume, so winner-gated exactly
    # like volume already is (tasks/volume_reconciliation.md): populated from whichever provider
    # won the bar's OHLC reconciliation, null if that provider didn't report it.
    wap: float | None = None
    trade_count: int | None = None
    # Quote group -- a different feed (the NBBO quote book, not the trade tape), not winner-gated:
    # populated from whichever provider reported it, independent of the OHLC vote.
    avg_bid: float | None = None
    avg_ask: float | None = None
    midpoint_open: float | None = None
    midpoint_high: float | None = None
    midpoint_low: float | None = None
    midpoint_close: float | None = None


class ProviderRole(Enum):
    """Mirrors dim_provider.role's CHECK constraint ('candidate', 'whistleblower', or 'advisor') --
    a closed set, unlike e.g. LoggingSink's category strings, so an Enum fits here the same way
    quant_data._internal.shared.diagnostics.TelemetryLevel does for the other closed-set string
    column in this codebase. ADVISOR (added in migration 011, e.g. 'manual'/'databento') can
    suggest a value but has no autonomous authoring rights -- unlike CANDIDATE, it can never win a
    bar through reconcile's automatic Tier 1-3 pass, only through an explicit human action."""

    CANDIDATE = "candidate"
    WHISTLEBLOWER = "whistleblower"
    ADVISOR = "advisor"


@dataclass
class PendingResolutionBar:
    """One provider's disputed staging value for a (bar, field group) still awaiting manual
    resolution (fact_pending_manual_resolution) -- a bar is pending precisely because its
    reporting providers disagree, so a single OHLCV per bar would hide the actual disagreement.
    field_group is currently always "ohlc" (the only active dim_field_group row), included for
    forward compatibility if a second field group is ever added. role is what actually lets a
    caller identify the reference value among possibly several candidates -- today's data is
    exactly one whistleblower (yfinance) plus one candidate (ibkr), but dim_provider isn't
    hardcoded to two rows, so don't assume exactly one candidate."""

    field_group: str
    provider: str
    role: ProviderRole
    bar: OHLCV


@dataclass
class RejectedWhistleblowerBar:
    """A whistleblower-reported staging value with data_quality=REJECTED -- a per-provider
    plausibility check found it implausible relative to its own series' neighbors (see
    tasks/yahoo_data_sanitization.md). Deliberately scoped to the whistleblower only, not a
    general per-provider rejected-bars feed: this exists to answer "did yfinance's own raw feed
    have a bad tick here," an audit/quality-monitoring question distinct from
    PendingResolutionBar's "the providers disagree, a human needs to decide" -- a rejected
    whistleblower value with an accepted candidate auto-resolves via Tier 1 and never reaches
    fact_pending_manual_resolution at all, so this is the only way to see it. No role field
    (always WHISTLEBLOWER by construction, unlike PendingResolutionBar which covers both sides)."""

    provider: str
    bar: OHLCV


class LoggingSink(Protocol):
    """Injectable logging contract -- a host application (e.g. quant-scratch) can pass its own
    logger into create_postgres_provider/PostgresDatabase/MarketData and quant_data writes
    through it instead of its own private Logger. Mirrors quant_data._internal.shared.diagnostics
    .DiagnosticsLogSink's method surface exactly, so any tpl-py-descended repo's own existing
    Logger already satisfies this structurally, with no changes needed on the host side.

    category defaults to the literal "general" here (not imported from
    quant_data._internal.shared.diagnostics.CATEGORY_GENERAL) so this module stays a
    dependency-graph leaf -- see CLAUDE.md's Architecture conventions, rule 8.
    """

    def diagnostic(self, message: str, category: str = "general") -> None: ...

    def info(self, message: str, category: str = "general") -> None: ...

    def warning(self, message: str, category: str = "general") -> None: ...

    def error(self, message: str, category: str = "general") -> None: ...

    def fatal(self, message: str, category: str = "general") -> None: ...

    def perf(self, description: str, elapsed_seconds: float) -> None: ...
