from __future__ import annotations

from datetime import date, datetime

from quant_data.client.market_data import MarketData
from quant_data.protocols import OHLCV, PendingResolutionBar
from tests.mocks.postgres import MockPostgresDatabase


def test_fetch_bars_delegates_to_provider():
    provider = MockPostgresDatabase()
    provider.write_bars([OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)])
    client = MarketData(provider)

    bars = client.fetch_bars("aapl", date(2026, 7, 24), date(2026, 7, 24))

    assert len(bars) == 1
    assert bars[0].ticker == "AAPL"
    assert bars[0].volume == 100


def test_fetch_pending_resolution_bars_delegates_to_provider():
    provider = MockPostgresDatabase()
    provider.pending_resolution_bars = [
        PendingResolutionBar(
            field_group="ohlc",
            provider="yfinance",
            bar=OHLCV(ticker="SPY", timestamp=datetime(2026, 8, 3, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100),
        ),
        PendingResolutionBar(
            field_group="ohlc",
            provider="ibkr",
            bar=OHLCV(ticker="SPY", timestamp=datetime(2026, 8, 3, 13, 30), open=1.1, high=2.1, low=0.6, close=1.6, volume=110),
        ),
    ]
    client = MarketData(provider)

    candidates = client.fetch_pending_resolution_bars("spy", date(2026, 8, 3), date(2026, 8, 3))

    assert len(candidates) == 2
    assert {candidate.provider for candidate in candidates} == {"yfinance", "ibkr"}
    assert all(candidate.field_group == "ohlc" for candidate in candidates)
    assert all(candidate.bar.ticker == "SPY" for candidate in candidates)


def test_close_delegates_to_provider():
    provider = MockPostgresDatabase()
    client = MarketData(provider)

    client.close()

    assert provider.closed


def test_has_no_write_method():
    assert not hasattr(MarketData, "write_bars")


def test_context_manager_closes_provider_on_exit():
    provider = MockPostgresDatabase()

    with MarketData(provider) as client:
        assert client is not None
        assert not provider.closed

    assert provider.closed


def test_accepts_an_injected_logger():
    # LoggingSink injection (quant-data#20) -- MarketData doesn't log anything of its own yet,
    # but must accept the parameter without error, matching create_postgres_provider's own
    # injection point.
    class _FakeLogger:
        def diagnostic(self, message: str, category: str = "general") -> None:
            pass

        def info(self, message: str, category: str = "general") -> None:
            pass

        def warning(self, message: str, category: str = "general") -> None:
            pass

        def error(self, message: str, category: str = "general") -> None:
            pass

        def fatal(self, message: str, category: str = "general") -> None:
            pass

        def perf(self, description: str, elapsed_seconds: float) -> None:
            pass

    provider = MockPostgresDatabase()

    client = MarketData(provider, logger=_FakeLogger())

    assert client is not None
