from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas
import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.yfinance import YahooFinanceIntraDay
from quant_data.protocols import DataQuality


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
def test_fetch_bars_flags_nan_rows_as_incomplete(mock_yfinance):
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

    bars = YahooFinanceIntraDay().fetch_bars("aapl", date(2026, 7, 24))

    assert len(bars) == 2
    assert bars[0].ticker == "AAPL"
    assert bars[0].data_quality == DataQuality.ACCEPTED
    assert bars[0].volume == 1000
    assert bars[1].data_quality == DataQuality.INCOMPLETE
    assert bars[1].volume == 0
    assert bars[1].open == 0.0


@patch("quant_data._internal.shared.providers.yfinance.yfinance")
def test_fetch_bars_flags_literal_zero_volume_as_incomplete(mock_yfinance):
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

    bars = YahooFinanceIntraDay().fetch_bars("aapl", date(2026, 7, 24))

    assert len(bars) == 1
    assert bars[0].volume == 0
    assert bars[0].data_quality == DataQuality.INCOMPLETE
    # A literal 0 is a real (if suspicious) OHLC reading, unlike the NaN case -- only volume
    # drives incomplete here, open/high/low/close shouldn't be coerced to 0.0.
    assert bars[0].open == 100.0


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
