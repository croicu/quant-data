from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Sliding-window rate limiter: at most requests_per_window calls within any rolling
    window_seconds span. Sits between ingest's orchestration loop and IntraDayProvider.fetch_bars
    (croicu/quant-data#28) -- pacing is a property of reaching a specific external service, not
    something baked into any one provider implementation.

    clock/sleep are constructor-injected (this repo's established pattern for anything touching
    the outside world, e.g. main()'s injectable `today` callable in ingest/cli.py) rather than
    monkeypatching the time module in tests."""

    def __init__(
        self,
        requests_per_window: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._requests_per_window = requests_per_window
        self._window_seconds = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._call_times: deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        while self._call_times and now - self._call_times[0] >= self._window_seconds:
            self._call_times.popleft()

        if len(self._call_times) >= self._requests_per_window:
            wait_seconds = self._window_seconds - (now - self._call_times[0])
            if wait_seconds > 0:
                self._sleep(wait_seconds)
            now = self._clock()
            while self._call_times and now - self._call_times[0] >= self._window_seconds:
                self._call_times.popleft()

        self._call_times.append(now)
