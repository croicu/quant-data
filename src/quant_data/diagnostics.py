from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum

# Not a closed enum: category is an open string so callers can introduce new categories
# without editing this file. CATEGORY_GENERAL is the only one every log call defaults to.
CATEGORY_GENERAL = "general"


class TelemetryLevel(Enum):
    VERBOSE = "verbose"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class TelemetryRecord:
    def __init__(
        self,
        timestamp: datetime,
        level: TelemetryLevel,
        message: str,
        category: str = CATEGORY_GENERAL,
    ) -> None:
        self.timestamp = timestamp
        self.level = level
        self.message = message
        self.category = category


class DiagnosticsLogSink:
    _pending: list[TelemetryRecord] = []

    def log(self, level: TelemetryLevel, message: str, category: str = CATEGORY_GENERAL) -> TelemetryRecord:
        record = TelemetryRecord(
            timestamp=datetime.now(timezone.utc),
            level=level,
            message=message,
            category=category,
        )
        DiagnosticsLogSink._pending.append(record)
        return record

    def flush(self) -> None:
        _logger = logging.getLogger(__name__)
        for record in DiagnosticsLogSink._pending:
            _logger.warning("%s: %s", record.timestamp.isoformat(), record.message)

    def clear(self) -> None:
        DiagnosticsLogSink._pending.clear()

    def drain(self) -> list[str]:
        messages = []
        for record in DiagnosticsLogSink._pending:
            messages.append(record.message)
        DiagnosticsLogSink._pending.clear()
        return messages

    def print(self, message: str) -> None:
        print(message)

    def diagnostic(self, message: str, category: str = CATEGORY_GENERAL) -> None:
        self.log(TelemetryLevel.VERBOSE, message, category)

    def info(self, message: str, category: str = CATEGORY_GENERAL) -> None:
        self.log(TelemetryLevel.INFO, message, category)

    def warning(self, message: str, category: str = CATEGORY_GENERAL) -> None:
        self.log(TelemetryLevel.WARNING, message, category)

    def error(self, message: str, category: str = CATEGORY_GENERAL) -> None:
        self.log(TelemetryLevel.ERROR, message, category)

    def fatal(self, message: str, category: str = CATEGORY_GENERAL) -> None:
        self.log(TelemetryLevel.CRITICAL, message, category)


_LEVEL_RANK: dict[TelemetryLevel, int] = {
    TelemetryLevel.VERBOSE: 0,
    TelemetryLevel.INFO: 1,
    TelemetryLevel.WARNING: 2,
    TelemetryLevel.ERROR: 3,
    TelemetryLevel.CRITICAL: 4,
}


_console_lock = threading.Lock()


class ConsoleLogSink(DiagnosticsLogSink):
    def __init__(
        self,
        min_level: TelemetryLevel = TelemetryLevel.ERROR,
        categories: list[str] | None = None,
        excluded_categories: list[str] | None = None,
    ) -> None:
        self._min_level = min_level
        self._categories = categories
        self._excluded_categories = excluded_categories

    def log(self, level: TelemetryLevel, message: str, category: str = CATEGORY_GENERAL) -> TelemetryRecord:
        record = super().log(level, message, category)
        level_passes = _LEVEL_RANK[level] >= _LEVEL_RANK[self._min_level]
        if self._categories:
            # Explicit (or debug-widened) allow-list: excluded_categories is inert here — a
            # category named in both would just be a no-op omission the user could've made
            # directly in the allow-list instead.
            category_passes = record.category in self._categories
        else:
            # Unfiltered ("show everything") state: excluded_categories becomes a deny-list
            # over the otherwise-open set.
            category_passes = not self._excluded_categories or record.category not in self._excluded_categories
        if level_passes and category_passes:
            with _console_lock:
                print(f"[{level.value.upper()}][{record.category}] {record.message}", flush=True)
        return record


class Logger:
    # Public

    @staticmethod
    def set_logger(value: DiagnosticsLogSink | None) -> None:
        if value is None:
            if len(Logger._sinks) > 1:
                Logger._sinks.pop()
        else:
            Logger._sinks.append(value)

    @staticmethod
    def log(level: TelemetryLevel, message: str, category: str = CATEGORY_GENERAL) -> TelemetryRecord:
        return Logger._sink().log(level, message, category)

    @staticmethod
    def flush() -> None:
        Logger._sink().flush()

    @staticmethod
    def clear() -> None:
        Logger._sink().clear()

    @staticmethod
    def drain() -> list[str]:
        return Logger._sink().drain()

    @staticmethod
    def print(message: str) -> None:
        Logger._sink().print(message)

    @staticmethod
    def diagnostic(message: str, category: str = CATEGORY_GENERAL) -> None:
        Logger._sink().diagnostic(message, category)

    @staticmethod
    def info(message: str, category: str = CATEGORY_GENERAL) -> None:
        Logger._sink().info(message, category)

    @staticmethod
    def warning(message: str, category: str = CATEGORY_GENERAL) -> None:
        Logger._sink().warning(message, category)

    @staticmethod
    def error(message: str, category: str = CATEGORY_GENERAL) -> None:
        Logger._sink().error(message, category)

    @staticmethod
    def fatal(message: str, category: str = CATEGORY_GENERAL) -> None:
        Logger._sink().fatal(message, category)

    @staticmethod
    def _reset() -> None:
        Logger._sinks = [DiagnosticsLogSink()]

    # Private

    @staticmethod
    def _sink() -> DiagnosticsLogSink:
        return Logger._sinks[-1]

    # Members

    _sinks: list[DiagnosticsLogSink] = [DiagnosticsLogSink()]
