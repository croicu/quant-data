from __future__ import annotations

from datetime import date, datetime, timedelta

from quant_data._internal.shared.providers.yfinance import YahooFinanceIntraDay
from quant_data.protocols import OHLCV


def _last_weekday(reference: date) -> date:
    result = reference
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def test_fetch_bars_returns_live_intraday_data_for_known_ticker():
    target_date = _last_weekday(datetime.now().date() - timedelta(days=1))

    bars = YahooFinanceIntraDay().fetch_bars("spy", target_date).bars

    assert len(bars) > 0
    assert isinstance(bars[0], OHLCV)
    assert bars[0].ticker == "SPY"
