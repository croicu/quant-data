from __future__ import annotations

from quant_data._internal.shared.providers.payload import raw_bars_payload


def test_raw_bars_payload_wraps_bar_dicts():
    bar = {
        "timestamp": "2026-07-23T13:30:00+00:00",
        "open": 745.06,
        "high": 745.84,
        "low": 744.07,
        "close": 745.53,
        "volume": 6319,
    }

    assert raw_bars_payload([bar]) == {"bars": [bar]}


def test_raw_bars_payload_handles_empty_list():
    assert raw_bars_payload([]) == {"bars": []}
