from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas
import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.yfinance import YahooFinanceIntraDay


def _history_frame(rows: list[dict]) -> pandas.DataFrame:
    timestamps = []
    open_values = []
    high_values = []
    low_values = []
    close_values = []
    volume_values = []
    for row in rows:
        timestamps.append(row["timestamp"])
        open_values.append(row["open"])
        high_values.append(row["high"])
        low_values.append(row["low"])
        close_values.append(row["close"])
        volume_values.append(row["volume"])

    index = pandas.DatetimeIndex(timestamps).tz_localize("America/New_York")
    return pandas.DataFrame(
        {
            "Open": open_values,
            "High": high_values,
            "Low": low_values,
            "Close": close_values,
            "Volume": volume_values,
        },
        index=index,
    )


@patch("quant_data._internal.shared.providers.yfinance.yfinance")
def test_fetch_bars_preserves_nan_as_none_in_raw_payload(mock_yfinance):
    # Pure fetch (croicu/quant-data#56) -- no incomplete/data-quality determination here anymore,
    # that moved to stage's yfinance parser (see tests/unit/test_stage_parsers.py). NaN is
    # preserved as JSON null rather than coerced to 0.0, so the parser sees the genuine raw signal.
    history = _history_frame(
        [
            {
                "timestamp": "2026-07-24 09:30:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
            },
            {
                "timestamp": "2026-07-24 09:31:00",
                "open": float("nan"),
                "high": float("nan"),
                "low": float("nan"),
                "close": float("nan"),
                "volume": float("nan"),
            },
        ]
    )
    mock_yfinance.Ticker.return_value.history.return_value = history

    payload = YahooFinanceIntraDay().fetch_bars("aapl", date(2026, 7, 24)).payload
    raw_bars = payload["bars"]

    assert len(raw_bars) == 2
    assert raw_bars[0]["volume"] == 1000
    assert raw_bars[1]["open"] is None
    assert raw_bars[1]["volume"] is None


@patch("quant_data._internal.shared.providers.yfinance.yfinance")
def test_fetch_bars_preserves_literal_zero_volume_in_raw_payload(mock_yfinance):
    history = _history_frame(
        [
            {
                "timestamp": "2026-07-24 04:00:00",
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "volume": 0,
            },
        ]
    )
    mock_yfinance.Ticker.return_value.history.return_value = history

    payload = YahooFinanceIntraDay().fetch_bars("aapl", date(2026, 7, 24)).payload
    raw_bars = payload["bars"]

    assert len(raw_bars) == 1
    # A literal 0 is a real reading, distinct from NaN -- preserved as-is; whether it counts as
    # incomplete is the parser's call, not the provider's.
    assert raw_bars[0]["volume"] == 0
    assert raw_bars[0]["open"] == 100.0


@patch("quant_data._internal.shared.providers.yfinance.yfinance")
def test_fetch_bars_raises_on_empty_history(mock_yfinance):
    mock_yfinance.Ticker.return_value.history.return_value = pandas.DataFrame()

    with pytest.raises(AppError):
        YahooFinanceIntraDay().fetch_bars("AAPL", date(2026, 7, 24))


@patch("quant_data._internal.shared.providers.yfinance.yfinance")
def test_fetch_bars_wraps_provider_exceptions(mock_yfinance):
    mock_yfinance.Ticker.return_value.history.side_effect = RuntimeError("network down")

    with pytest.raises(AppError):
        YahooFinanceIntraDay().fetch_bars("AAPL", date(2026, 7, 24))
