from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.ibkr import IBKRIntraDay
from quant_data.protocols import DataQuality


@dataclass
class _FakeBar:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _connected_provider(mock_ib_class) -> IBKRIntraDay:
    provider = IBKRIntraDay(host="127.0.0.1", port=4002, client_id=7)
    mock_ib_class.return_value.qualifyContracts.return_value = [MagicMock()]
    provider.connect()
    return provider


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_connect_skips_startup_fetch_and_uses_given_endpoint(mock_ib_class):
    provider = IBKRIntraDay(host="10.0.0.5", port=4002, client_id=3)

    provider.connect()

    mock_ib = mock_ib_class.return_value
    mock_ib.connect.assert_called_once()
    _, kwargs = mock_ib.connect.call_args
    assert mock_ib.connect.call_args.args[0] == "10.0.0.5"
    assert mock_ib.connect.call_args.args[1] == 4002
    assert kwargs["clientId"] == 3
    assert kwargs["fetchFields"].value == 0


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_connect_is_idempotent(mock_ib_class):
    provider = IBKRIntraDay()

    provider.connect()
    provider.connect()

    mock_ib_class.return_value.connect.assert_called_once()


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_connect_wraps_provider_exceptions(mock_ib_class):
    mock_ib_class.return_value.connect.side_effect = RuntimeError("gateway unreachable")

    with pytest.raises(AppError):
        IBKRIntraDay().connect()


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_close_disconnects_and_allows_reconnect(mock_ib_class):
    provider = IBKRIntraDay()
    provider.connect()

    provider.close()

    mock_ib_class.return_value.disconnect.assert_called_once()
    provider.connect()
    assert mock_ib_class.return_value.connect.call_count == 2


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_close_before_connect_is_a_no_op(mock_ib_class):
    IBKRIntraDay().close()

    mock_ib_class.return_value.disconnect.assert_not_called()


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_raises_if_not_connected(mock_ib_class):
    with pytest.raises(AppError):
        IBKRIntraDay().fetch_bars("AAPL", date(2026, 7, 24))


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_raises_if_contract_not_qualified(mock_ib_class):
    provider = IBKRIntraDay()
    mock_ib_class.return_value.qualifyContracts.return_value = []
    provider.connect()

    with pytest.raises(AppError):
        provider.fetch_bars("BADTICKER", date(2026, 7, 24))


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_raises_on_no_bars(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = []

    with pytest.raises(AppError):
        provider.fetch_bars("AAPL", date(2026, 7, 24))


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_wraps_provider_exceptions(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.side_effect = RuntimeError("pacing violation")

    with pytest.raises(AppError):
        provider.fetch_bars("AAPL", date(2026, 7, 24))


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_maps_bars_to_ohlcv(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(
            date=datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc),
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=1234,
        ),
        _FakeBar(
            date=datetime(2026, 7, 24, 13, 31, tzinfo=timezone.utc),
            open=100.5,
            high=100.5,
            low=100.5,
            close=100.5,
            volume=0,
        ),
    ]

    bars = provider.fetch_bars("aapl", date(2026, 7, 24)).bars

    assert len(bars) == 2
    assert bars[0].ticker == "AAPL"
    assert bars[0].timestamp == datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    assert bars[0].volume == 1234
    assert bars[0].data_quality == DataQuality.ACCEPTED
    # A real zero-volume IBKR bar (no trades that minute) is not treated as incomplete, unlike
    # Yahoo's synthetic-gap heuristic.
    assert bars[1].volume == 0
    assert bars[1].data_quality == DataQuality.ACCEPTED


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_normalizes_naive_timestamp_to_utc(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(
            date=datetime(2026, 7, 24, 13, 30),
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=10,
        ),
    ]

    bars = provider.fetch_bars("AAPL", date(2026, 7, 24)).bars

    assert bars[0].timestamp.tzinfo is not None
    assert bars[0].timestamp == datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
