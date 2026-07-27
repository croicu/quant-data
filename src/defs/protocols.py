"""Persisted/shared data contracts.

Dataclasses only — no methods, no logic. Behavior that operates on these types belongs in a
dedicated entity/service layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
