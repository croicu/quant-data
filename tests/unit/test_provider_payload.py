from __future__ import annotations

from datetime import datetime, timezone

from quant_data._internal.shared.providers.payload import parsed_bars_payload
from quant_data.protocols import OHLCV, DataQuality


def test_parsed_bars_payload_serializes_every_field():
    bar = OHLCV(
        ticker="SPY",
        timestamp=datetime(2026, 7, 23, 13, 30, tzinfo=timezone.utc),
        open=745.06,
        high=745.84,
        low=744.07,
        close=745.53,
        volume=6319,
        data_quality=DataQuality.ACCEPTED,
    )

    payload = parsed_bars_payload([bar])

    assert payload == {
        "bars": [
            {
                "ticker": "SPY",
                "timestamp": "2026-07-23T13:30:00+00:00",
                "open": 745.06,
                "high": 745.84,
                "low": 744.07,
                "close": 745.53,
                "volume": 6319,
                "data_quality": "accepted",
            }
        ]
    }


def test_parsed_bars_payload_handles_empty_list():
    assert parsed_bars_payload([]) == {"bars": []}
