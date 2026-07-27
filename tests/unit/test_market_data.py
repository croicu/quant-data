from __future__ import annotations

from datetime import date, datetime

from quant_data.client.market_data import MarketData
from quant_data.protocols import OHLCV
from tests.mocks.postgres import MockPostgresDatabase


def test_fetch_bars_delegates_to_provider():
    provider = MockPostgresDatabase()
    provider.write_bars([OHLCV(ticker="AAPL", timestamp=datetime(2026, 7, 24, 13, 30), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)])
    client = MarketData(provider)

    bars = client.fetch_bars("aapl", date(2026, 7, 24), date(2026, 7, 24))

    assert len(bars) == 1
    assert bars[0].ticker == "AAPL"
    assert bars[0].volume == 100


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
