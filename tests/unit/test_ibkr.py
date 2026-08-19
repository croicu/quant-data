from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.ibkr import IBKRIntraDay


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
def test_fetch_bars_returns_raw_payload(mock_ib_class):
    # Pure fetch (croicu/quant-data#56) -- no OHLCV parsing here anymore, that moved to stage's
    # ibkr parser (see tests/unit/test_stage_parsers.py).
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

    payload = provider.fetch_bars("aapl", date(2026, 7, 24)).payload
    raw_bars = payload["bars"]

    assert len(raw_bars) == 2
    assert raw_bars[0]["timestamp"] == "2026-07-24T13:30:00+00:00"
    assert raw_bars[0]["volume"] == 1234
    assert raw_bars[1]["volume"] == 0


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

    payload = provider.fetch_bars("AAPL", date(2026, 7, 24)).payload

    assert payload["bars"][0]["timestamp"] == "2026-07-24T13:30:00+00:00"


# --- Multi-method fetching (croicu/quant-data#60) ---


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_default_methods_is_trades_bid_ask_and_midpoint(mock_ib_class):
    assert IBKRIntraDay.DEFAULT_METHODS == ["TRADES", "BID_ASK", "MIDPOINT"]


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_defaults_to_first_default_method(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(date=datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc), open=100.0, high=101.0, low=99.5, close=100.5, volume=10),
    ]

    result = provider.fetch_bars("AAPL", date(2026, 7, 24))

    assert result.method == "TRADES"
    _, kwargs = mock_ib_class.return_value.reqHistoricalData.call_args
    assert kwargs["whatToShow"] == "TRADES"


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_requests_the_given_method(mock_ib_class):
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(date=datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc), open=778.1, high=778.3, low=778.0, close=778.2, volume=-1),
    ]

    result = provider.fetch_bars("AAPL", date(2026, 7, 24), method="BID_ASK")

    assert result.method == "BID_ASK"
    _, kwargs = mock_ib_class.return_value.reqHistoricalData.call_args
    assert kwargs["whatToShow"] == "BID_ASK"


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_serializes_bid_ask_bars_with_honest_field_names(mock_ib_class):
    # BID_ASK bars aren't trade bars -- .open/.close are avg bid/avg ask, not a real OHLC
    # open/close, and .volume is meaningless (-1) on a quote-type bar (tasks/
    # ingestion_variable_inventory.md Sec 1.3) -- so the archived payload uses its own field
    # names instead of borrowing TRADES' OHLCV vocabulary.
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(date=datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc), open=778.12, high=778.26, low=778.0, close=778.18, volume=-1),
    ]

    payload = provider.fetch_bars("AAPL", date(2026, 7, 24), method="BID_ASK").payload
    bar = payload["bars"][0]

    assert bar["avg_bid"] == 778.12
    assert bar["avg_ask"] == 778.18
    assert bar["high"] == 778.26
    assert bar["low"] == 778.0
    assert "open" not in bar
    assert "close" not in bar
    assert "volume" not in bar


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_serializes_midpoint_bars_as_real_ohlc(mock_ib_class):
    # Unlike BID_ASK, MIDPOINT bars are a genuine OHLC series of the bid/ask midpoint price, so
    # they keep the standard OHLC field names -- just no volume, same as BID_ASK.
    provider = _connected_provider(mock_ib_class)
    mock_ib_class.return_value.reqHistoricalData.return_value = [
        _FakeBar(date=datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc), open=778.15, high=778.3, low=778.0, close=778.2, volume=-1),
    ]

    payload = provider.fetch_bars("AAPL", date(2026, 7, 24), method="MIDPOINT").payload
    bar = payload["bars"][0]

    assert bar["open"] == 778.15
    assert bar["high"] == 778.3
    assert bar["low"] == 778.0
    assert bar["close"] == 778.2
    assert "volume" not in bar
    assert "avg_bid" not in bar


@patch("quant_data._internal.shared.providers.ibkr.IB")
def test_fetch_bars_raises_on_unrecognized_method(mock_ib_class):
    provider = _connected_provider(mock_ib_class)

    with pytest.raises(AppError):
        provider.fetch_bars("AAPL", date(2026, 7, 24), method="ADJUSTED_LAST")
