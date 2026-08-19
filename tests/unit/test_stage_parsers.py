from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data.protocols import OHLCV, DataQuality
from stage.parsers import apply_supplementary_payload, ibkr_parser, massive_parser, parse_payload, yfinance_parser


def test_yfinance_parser_flags_none_fields_as_incomplete():
    payload = {
        "bars": [
            {"timestamp": "2026-07-24T09:30:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
            {"timestamp": "2026-07-24T09:31:00+00:00", "open": None, "high": None, "low": None, "close": None, "volume": None},
        ]
    }

    bars = yfinance_parser.parse(payload, "aapl")

    assert len(bars) == 2
    assert bars[0].ticker == "AAPL"
    assert bars[0].data_quality == DataQuality.ACCEPTED
    assert bars[1].data_quality == DataQuality.INCOMPLETE
    assert bars[1].open == 0.0
    assert bars[1].volume == 0


def test_yfinance_parser_flags_literal_zero_volume_as_incomplete():
    payload = {"bars": [{"timestamp": "2026-07-24T04:00:00+00:00", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 0}]}

    bars = yfinance_parser.parse(payload, "aapl")

    assert len(bars) == 1
    assert bars[0].volume == 0
    assert bars[0].data_quality == DataQuality.INCOMPLETE
    # A literal 0 is a real (if suspicious) OHLC reading, unlike a None field -- only volume
    # drives incomplete here, open/high/low/close aren't coerced.
    assert bars[0].open == 100.0


def test_ibkr_parser_marks_every_bar_accepted():
    payload = {
        "bars": [
            {"timestamp": "2026-07-24T13:30:00+00:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1234},
            {"timestamp": "2026-07-24T13:31:00+00:00", "open": 100.5, "high": 100.5, "low": 100.5, "close": 100.5, "volume": 0},
        ]
    }

    bars = ibkr_parser.parse(payload, "aapl")

    assert len(bars) == 2
    assert bars[0].ticker == "AAPL"
    assert bars[0].timestamp == datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    # A real zero-volume IBKR bar (no trades that minute) is not treated as incomplete, unlike
    # yfinance's synthetic-gap heuristic.
    assert bars[1].volume == 0
    assert bars[1].data_quality == DataQuality.ACCEPTED


_SAMPLE_MASSIVE_PAYLOAD = {
    "ticker": "SPY",
    "results": [
        {"v": 9533.002, "vw": 745.14, "o": 745.6, "c": 745.4, "h": 745.73, "l": 744.07, "t": 1785484860000, "n": 288},
        {"v": 6319.407237, "vw": 745.3721, "o": 745.06, "c": 745.53, "h": 745.84, "l": 744.22, "t": 1785484800000, "n": 454},
    ],
    "status": "OK",
}


def test_massive_parser_parses_and_sorts_chronologically():
    bars = massive_parser.parse(_SAMPLE_MASSIVE_PAYLOAD, "spy")

    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[0].ticker == "SPY"
    assert bars[0].open == 745.06
    assert bars[0].volume == 6319  # truncated from the fractional 6319.407237
    assert bars[1].close == 745.4
    assert bars[0].data_quality == DataQuality.ACCEPTED  # no synthetic/NaN placeholder rows, like IBKR


def test_massive_parser_extracts_wap_and_trade_count():
    # croicu/quant-data#61 trade group -- free on the same aggregates response, no extra archive
    # read needed.
    bars = massive_parser.parse(_SAMPLE_MASSIVE_PAYLOAD, "spy")

    assert bars[0].wap == 745.3721
    assert bars[0].trade_count == 454
    assert bars[1].wap == 745.14
    assert bars[1].trade_count == 288


def test_massive_parser_leaves_wap_and_trade_count_none_when_absent():
    payload = {"results": [{"v": 100, "o": 1.0, "c": 1.0, "h": 1.0, "l": 1.0, "t": 1785484860000}]}

    bars = massive_parser.parse(payload, "spy")

    assert bars[0].wap is None
    assert bars[0].trade_count is None


def test_massive_parser_raises_when_no_results():
    with pytest.raises(AppError, match="no 'results'"):
        massive_parser.parse({"ticker": "SPY", "status": "OK"}, "spy")


def test_ibkr_parser_parse_bid_ask():
    payload = {
        "bars": [
            {"timestamp": "2026-07-24T13:30:00+00:00", "avg_bid": 100.0, "avg_ask": 100.1, "high": 100.2, "low": 99.9},
        ]
    }

    result = ibkr_parser.parse_bid_ask(payload)

    assert result == {datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc): (100.0, 100.1)}


def test_ibkr_parser_parse_midpoint():
    payload = {
        "bars": [
            {"timestamp": "2026-07-24T13:30:00+00:00", "open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1},
        ]
    }

    result = ibkr_parser.parse_midpoint(payload)

    assert result == {datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc): (100.0, 100.2, 99.9, 100.1)}


def test_apply_supplementary_payload_merges_bid_ask_into_matching_bar():
    timestamp = datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    bar = OHLCV(ticker="AAPL", timestamp=timestamp, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
    bars_by_timestamp = {timestamp: bar}
    payload = {"bars": [{"timestamp": "2026-07-24T13:30:00+00:00", "avg_bid": 100.0, "avg_ask": 100.1, "high": 100.2, "low": 99.9}]}

    apply_supplementary_payload("ibkr", "BID_ASK", payload, bars_by_timestamp)

    assert bar.avg_bid == 100.0
    assert bar.avg_ask == 100.1
    assert bar.midpoint_open is None  # untouched -- BID_ASK doesn't populate the quote midpoint fields


def test_apply_supplementary_payload_merges_midpoint_into_matching_bar():
    timestamp = datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    bar = OHLCV(ticker="AAPL", timestamp=timestamp, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
    bars_by_timestamp = {timestamp: bar}
    payload = {"bars": [{"timestamp": "2026-07-24T13:30:00+00:00", "open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1}]}

    apply_supplementary_payload("ibkr", "MIDPOINT", payload, bars_by_timestamp)

    assert (bar.midpoint_open, bar.midpoint_high, bar.midpoint_low, bar.midpoint_close) == (100.0, 100.2, 99.9, 100.1)


def test_apply_supplementary_payload_skips_timestamp_with_no_matching_primary_bar():
    bars_by_timestamp: dict = {}
    payload = {"bars": [{"timestamp": "2026-07-24T13:30:00+00:00", "avg_bid": 100.0, "avg_ask": 100.1, "high": 100.2, "low": 99.9}]}

    apply_supplementary_payload("ibkr", "BID_ASK", payload, bars_by_timestamp)  # no raise

    assert bars_by_timestamp == {}


def test_apply_supplementary_payload_is_a_no_op_for_unregistered_provider_method():
    timestamp = datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
    bar = OHLCV(ticker="AAPL", timestamp=timestamp, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
    bars_by_timestamp = {timestamp: bar}

    apply_supplementary_payload("massive", "aggregates", {}, bars_by_timestamp)  # no raise, no-op

    assert bar.avg_bid is None


def test_parse_payload_dispatches_by_provider_name():
    payload = {"bars": [{"timestamp": "2026-07-24T13:30:00+00:00", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}]}

    bars = parse_payload("ibkr", payload, "aapl")

    assert len(bars) == 1


def test_parse_payload_raises_for_unknown_provider():
    with pytest.raises(AppError, match="No stage parser registered"):
        parse_payload("not-a-real-provider", {}, "aapl")
