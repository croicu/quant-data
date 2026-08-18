from __future__ import annotations

from datetime import date, datetime, timedelta

from quant_data._internal.shared.providers.ibkr import IBKRIntraDay
from stage.parsers import ibkr_parser


def _last_weekday(reference: date) -> date:
    result = reference
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def test_fetch_bars_returns_live_intraday_data_for_known_ticker():
    # Requires a locally running IB Gateway/TWS reachable at 127.0.0.1:4002 (paper) -- unlike
    # test_yfinance.py's integration test, plain network access alone isn't enough here.
    target_date = _last_weekday(datetime.now().date() - timedelta(days=1))

    provider = IBKRIntraDay()
    provider.connect()
    try:
        payload = provider.fetch_bars("spy", target_date).payload
    finally:
        provider.close()

    bars = ibkr_parser.parse(payload, "spy")

    assert len(bars) > 0
    assert bars[0].ticker == "SPY"
