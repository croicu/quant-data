from __future__ import annotations

from datetime import date

import pytest
import requests
from requests.exceptions import HTTPError

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.providers.massive import MassiveIntraDay
from quant_data.protocols import DataQuality

_TARGET_DATE = date(2026, 7, 31)

# Shape confirmed against the real API by croicu/quant-scratch#24's prototype (live-verified
# 2026-08-15).
_SAMPLE_PAYLOAD = {
    "ticker": "SPY",
    "queryCount": 2,
    "resultsCount": 2,
    "adjusted": True,
    "results": [
        {"v": 9533.002, "vw": 745.14, "o": 745.6, "c": 745.4, "h": 745.73, "l": 744.07, "t": 1785484860000, "n": 288},
        {"v": 6319.407237, "vw": 745.3721, "o": 745.06, "c": 745.53, "h": 745.84, "l": 744.22, "t": 1785484800000, "n": 454},
    ],
    "status": "OK",
    "request_id": "test-request-id",
    "count": 2,
}


def _http_error(status_code: int) -> HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return HTTPError(f"{status_code} Client Error", response=response)


class _FixedPayloadRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __call__(self, url: str, params: dict) -> dict:
        return self._payload


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def test_fetch_bars_parses_and_sorts_chronologically():
    provider = MassiveIntraDay(api_key="test-key", request_fn=_FixedPayloadRequest(_SAMPLE_PAYLOAD))

    bars = provider.fetch_bars("spy", _TARGET_DATE)

    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[0].ticker == "SPY"
    assert bars[0].open == 745.06
    assert bars[0].volume == 6319  # truncated from the fractional 6319.407237
    assert bars[1].close == 745.4
    assert bars[0].data_quality == DataQuality.ACCEPTED  # no synthetic/NaN placeholder rows, like IBKR


def test_fetch_bars_sends_expected_url_and_params():
    captured = {}

    def fake_request(url: str, params: dict) -> dict:
        captured["url"] = url
        captured["params"] = params
        return _SAMPLE_PAYLOAD

    provider = MassiveIntraDay(api_key="test-key", request_fn=fake_request)
    provider.fetch_bars("spy", _TARGET_DATE)

    assert captured["url"] == "https://api.massive.com/v2/aggs/ticker/SPY/range/1/minute/2026-07-31/2026-07-31"
    assert captured["params"] == {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": "test-key"}


def test_fetch_bars_raises_on_error_status():
    payload = {"status": "ERROR", "error": "Unknown API Key"}
    provider = MassiveIntraDay(api_key="bad-key", request_fn=_FixedPayloadRequest(payload))

    with pytest.raises(AppError, match="Unknown API Key"):
        provider.fetch_bars("spy", _TARGET_DATE)


def test_fetch_bars_raises_when_no_results():
    # Real shape for a weekend/holiday or invalid ticker: status OK, no 'results' key at all.
    payload = {"ticker": "SPY", "queryCount": 0, "resultsCount": 0, "adjusted": True, "status": "OK"}
    provider = MassiveIntraDay(api_key="test-key", request_fn=_FixedPayloadRequest(payload))

    with pytest.raises(AppError, match="No data available"):
        provider.fetch_bars("spy", _TARGET_DATE)


def test_fetch_bars_wraps_request_exception():
    def failing_request(url: str, params: dict) -> dict:
        raise ConnectionError("network down")

    provider = MassiveIntraDay(api_key="test-key", request_fn=failing_request)

    with pytest.raises(AppError, match="network down"):
        provider.fetch_bars("spy", _TARGET_DATE)


def test_fetch_bars_retries_on_rate_limit_then_succeeds():
    responses = [_http_error(429), _http_error(429), _SAMPLE_PAYLOAD]

    def fake_request(url: str, params: dict) -> dict:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    sleep = _RecordingSleep()
    provider = MassiveIntraDay(api_key="test-key", request_fn=fake_request, sleep_fn=sleep)

    bars = provider.fetch_bars("spy", _TARGET_DATE)

    assert len(bars) == 2
    assert sleep.calls == [15.0, 15.0]


def test_fetch_bars_raises_after_exhausting_retries_on_persistent_rate_limit():
    def always_rate_limited(url: str, params: dict) -> dict:
        raise _http_error(429)

    sleep = _RecordingSleep()
    provider = MassiveIntraDay(api_key="test-key", request_fn=always_rate_limited, sleep_fn=sleep)

    with pytest.raises(AppError, match="still rate-limited"):
        provider.fetch_bars("spy", _TARGET_DATE)

    # 3 attempts total (default max_attempts), sleeping between each but not after the last.
    assert sleep.calls == [15.0, 15.0]


def test_fetch_bars_does_not_retry_on_non_rate_limit_http_error():
    call_count = {"count": 0}

    def forbidden(url: str, params: dict) -> dict:
        call_count["count"] += 1
        raise _http_error(403)

    sleep = _RecordingSleep()
    provider = MassiveIntraDay(api_key="test-key", request_fn=forbidden, sleep_fn=sleep)

    with pytest.raises(AppError, match="403"):
        provider.fetch_bars("spy", _TARGET_DATE)

    assert call_count["count"] == 1
    assert sleep.calls == []


def test_fetch_bars_respects_custom_max_attempts_and_retry_delay():
    def always_rate_limited(url: str, params: dict) -> dict:
        raise _http_error(429)

    sleep = _RecordingSleep()
    provider = MassiveIntraDay(api_key="test-key", request_fn=always_rate_limited, max_attempts=2, retry_delay_seconds=5.0, sleep_fn=sleep)

    with pytest.raises(AppError, match="after 2 attempts"):
        provider.fetch_bars("spy", _TARGET_DATE)

    assert sleep.calls == [5.0]
