from __future__ import annotations

from contextlib import contextmanager

from .diagnostics import Logger, TelemetryLevel, TelemetryRecord


@contextmanager
def telemetry_session():
    try:
        yield
    finally:
        Logger.flush()
        Logger.clear()


class AppError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.record: TelemetryRecord = Logger.log(TelemetryLevel.WARNING, message)


class TaskError(AppError):
    pass
