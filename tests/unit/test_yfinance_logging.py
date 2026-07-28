from __future__ import annotations

import logging
import re

from quant_data._internal.shared.diagnostics import TelemetryLevel
from quant_data._internal.shared.providers.yfinance_logging import (
    CATEGORY_YFINANCE,
    YFinanceLoggingAdapter,
    YFinanceLogRule,
    _YFinanceLogHandler,
    install_log_capture,
)


def test_classify_matches_known_pattern_as_info():
    adapter = YFinanceLoggingAdapter()

    level = adapter.classify("$SPY: possibly delisted; no price data found (1m 2026-07-25 -> 2026-07-26)")

    assert level == TelemetryLevel.INFO


def test_classify_falls_back_to_default_level_for_unrecognized_message():
    adapter = YFinanceLoggingAdapter()

    level = adapter.classify("some brand new yfinance message never seen before")

    assert level == TelemetryLevel.WARNING


def test_classify_supports_custom_rules_and_default_level():
    adapter = YFinanceLoggingAdapter(
        rules=[YFinanceLogRule(pattern=re.compile(r"rate limit"), level=TelemetryLevel.ERROR)],
        default_level=TelemetryLevel.VERBOSE,
    )

    assert adapter.classify("Too many requests, rate limited") == TelemetryLevel.ERROR
    assert adapter.classify("anything else") == TelemetryLevel.VERBOSE


def test_handle_logs_classified_level_message_and_category():
    calls = []

    def fake_log(level, message, category):
        calls.append((level, message, category))

    adapter = YFinanceLoggingAdapter(log=fake_log)

    adapter.handle("$AAPL: possibly delisted; no price data found")

    assert calls == [(TelemetryLevel.INFO, "$AAPL: possibly delisted; no price data found", CATEGORY_YFINANCE)]


def test_handler_forwards_record_message_to_adapter():
    handled_messages = []

    class RecordingAdapter:
        def handle(self, message: str) -> None:
            handled_messages.append(message)

    handler = _YFinanceLogHandler(RecordingAdapter())
    record = logging.LogRecord(
        name="yfinance",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="$SPY: possibly delisted; no price data found",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    assert handled_messages == ["$SPY: possibly delisted; no price data found"]


def test_install_log_capture_is_idempotent():
    # providers/yfinance.py already calls install_log_capture() at module import time (and by the
    # time this test runs, something in the suite has always already imported it) -- calling it
    # again here must not attach a second handler.
    install_log_capture()
    yfinance_logger = logging.getLogger("yfinance")
    handler_count_before = len(yfinance_logger.handlers)

    install_log_capture()

    assert len(yfinance_logger.handlers) == handler_count_before
    assert handler_count_before >= 1

    found_capture_handler = False
    for handler in yfinance_logger.handlers:
        if isinstance(handler, _YFinanceLogHandler):
            found_capture_handler = True
    assert found_capture_handler
    assert yfinance_logger.propagate is False
