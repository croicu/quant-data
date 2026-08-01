"""Public contracts: persisted/shared data (dataclasses) and behavioral Protocols meant for a
consumer to actually implement/inject (as opposed to _internal/contracts.py's Protocols, which
wire quant_data's own internals together and are never imported by consumers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
