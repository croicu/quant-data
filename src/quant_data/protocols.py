"""Public contracts: persisted/shared data (dataclasses) and behavioral Protocols meant for a
consumer to actually implement/inject (as opposed to _internal/contracts.py's Protocols, which
wire quant_data's own internals together and are never imported by consumers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


@dataclass
class OHLCV:
    ticker: str
    timestamp: datetime  # timezone-aware, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int
    incomplete: bool = False


class ProviderRole(Enum):
    """Mirrors dim_provider.role's CHECK constraint ('candidate' or 'whistleblower') -- a closed
    set, unlike e.g. LoggingSink's category strings, so an Enum fits here the same way
    quant_data._internal.shared.diagnostics.TelemetryLevel does for the other closed-set string
    column in this codebase."""

    CANDIDATE = "candidate"
    WHISTLEBLOWER = "whistleblower"


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
