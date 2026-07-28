from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from ..diagnostics import Logger, TelemetryLevel, TelemetryRecord

CATEGORY_YFINANCE = "yfinance"


@dataclass
class YFinanceLogRule:
    pattern: re.Pattern[str]
    level: TelemetryLevel


# Only one yfinance message has actually been observed in practice so far ("possibly delisted",
# seen when a --catch-up run crossed a weekend). More rules get added here as new messages are
# actually seen, rather than guessed at up front. Anything unmatched falls back to
# _DEFAULT_LEVEL -- safer to over- than under-report a yfinance message we don't recognize yet.
_DEFAULT_RULES: list[YFinanceLogRule] = [
    YFinanceLogRule(pattern=re.compile(r"possibly delisted"), level=TelemetryLevel.INFO),
]
_DEFAULT_LEVEL = TelemetryLevel.WARNING


class YFinanceLoggingAdapter:
    """Classifies yfinance's own log messages and re-emits them through our own Logger."""

    def __init__(
        self,
        rules: list[YFinanceLogRule] | None = None,
        default_level: TelemetryLevel = _DEFAULT_LEVEL,
        log: Callable[[TelemetryLevel, str, str], TelemetryRecord] = Logger.log,
    ) -> None:
        self._rules = _DEFAULT_RULES if rules is None else rules
        self._default_level = default_level
        self._log = log

    def classify(self, message: str) -> TelemetryLevel:
        for rule in self._rules:
            if rule.pattern.search(message):
                return rule.level
        return self._default_level

    def handle(self, message: str) -> None:
        level = self.classify(message)
        self._log(level, message, CATEGORY_YFINANCE)


class _YFinanceLogHandler(logging.Handler):
    """Bridges Python's stdlib logging (what yfinance itself uses) to a YFinanceLoggingAdapter."""

    def __init__(self, adapter: YFinanceLoggingAdapter) -> None:
        super().__init__()
        self._adapter = adapter

    def emit(self, record: logging.LogRecord) -> None:
        self._adapter.handle(record.getMessage())


_installed = False


def install_log_capture(adapter: YFinanceLoggingAdapter | None = None) -> None:
    """Redirects logging.getLogger('yfinance') into our own Logger instead of stderr.

    Idempotent -- safe to call more than once (only the first call actually installs anything),
    since this is invoked at module import time and modules can be imported more than once across
    a test session.
    """
    global _installed
    if _installed:
        return

    active_adapter = YFinanceLoggingAdapter() if adapter is None else adapter

    yfinance_logger = logging.getLogger("yfinance")
    # yfinance doesn't call setLevel on its own logger unless its own debug mode is explicitly
    # enabled (see yfinance/utils.py), so its effective level otherwise cascades from the root
    # logger's default (WARNING) -- lowering it here ensures every message yfinance emits reaches
    # our adapter to classify, rather than some being silently dropped before we ever see them.
    yfinance_logger.setLevel(logging.DEBUG)
    # Without this, a message we handle would also propagate to the root logger's lastResort
    # handler and print to stderr a second time.
    yfinance_logger.propagate = False
    yfinance_logger.addHandler(_YFinanceLogHandler(active_adapter))

    _installed = True
