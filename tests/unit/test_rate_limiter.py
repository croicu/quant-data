from __future__ import annotations

from ingest.rate_limiter import RateLimiter


class _FakeClock:
    """Manually-advanced fake clock/sleep pair -- sleep() advances the same clock the limiter
    reads from, simulating real elapsed time without an actual wall-clock wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


def test_acquire_does_not_sleep_under_the_limit():
    fake = _FakeClock()
    limiter = RateLimiter(requests_per_window=3, window_seconds=10, clock=fake.clock, sleep=fake.sleep)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert fake.sleep_calls == []


def test_acquire_sleeps_the_remaining_window_when_limit_reached():
    fake = _FakeClock()
    limiter = RateLimiter(requests_per_window=2, window_seconds=10, clock=fake.clock, sleep=fake.sleep)

    limiter.acquire()  # t=0
    fake.now = 3.0
    limiter.acquire()  # t=3, fills the window (2/2)

    limiter.acquire()  # 3rd call must wait until the t=0 call ages out at t=10 -- 7 seconds

    assert fake.sleep_calls == [7.0]


def test_acquire_does_not_sleep_once_enough_time_has_passed():
    fake = _FakeClock()
    limiter = RateLimiter(requests_per_window=2, window_seconds=10, clock=fake.clock, sleep=fake.sleep)

    limiter.acquire()  # t=0
    limiter.acquire()  # t=0, fills the window (2/2)

    fake.now = 15.0  # both calls have aged out of the 10s window
    limiter.acquire()

    assert fake.sleep_calls == []
