from __future__ import annotations

from datetime import date, datetime, timedelta

from quant_data._internal.shared.providers.yfinance import YahooFinanceIntraDay
from stage.parsers import yfinance_parser


def _last_weekday(reference: date) -> date:
    result = reference
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def test_fetch_bars_returns_live_intraday_data_for_known_ticker():
    target_date = _last_weekday(datetime.now().date() - timedelta(days=1))

    payload = YahooFinanceIntraDay().fetch_bars("spy", target_date).payload
    bars = yfinance_parser.parse(payload, "spy")

    assert len(bars) > 0
    assert bars[0].ticker == "SPY"
