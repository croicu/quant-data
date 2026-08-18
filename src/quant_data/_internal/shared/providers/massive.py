from __future__ import annotations

import time as time_module
from collections.abc import Callable
from datetime import date

import requests
from requests.exceptions import HTTPError

from quant_data._internal.contracts import PayloadKind, ProviderFetchResult

from ..diagnostics import Logger
from ..errors import AppError

CATEGORY_MASSIVE = "massive"

# Massive (formerly Polygon.io -- polygon.io now 301-redirects here, same /v2/aggs/... API shape).
BASE_URL = "https://api.massive.com"

# Massive's free Basic tier documents 5 API calls/minute -- quant-data's own pre-emptive
# RateLimitSettings (settings.massive.rateLimit, applied by ingest/cli.py before fetch_bars is ever
# called) is the primary defense, but croicu/quant-scratch#24's prototype found the documented
# limit isn't strictly enforced in practice, so a 429 mid-fetch is treated as a transient condition
# worth retrying rather than "no data for this day" -- retrying gives the per-minute window a
# chance to clear rather than permanently dropping a real trading day. 15s is a guess at a
# safe-enough spacing (60s/5 calls = 12s minimum, padded slightly), not a measured value.
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_SECONDS = 15.0
_RATE_LIMIT_STATUS = 429


class MassiveIntraDay:
    """IntraDayProvider backed by Massive's REST API -- plain requests.get, no SDK (adapted from
    croicu/quant-scratch#24's confirmed-working prototype, src/shared/providers/massive.py).
    Stateless per-call HTTP, like YahooFinanceIntraDay -- connect()/close() are no-ops, unlike
    IBKRIntraDay's persistent Gateway connection.
    """

    FETCH_VERSION = "1"

    def __init__(
        self,
        api_key: str,
        request_fn: Callable[[str, dict], dict] | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: float = _DEFAULT_RETRY_DELAY_SECONDS,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._request = _request if request_fn is None else request_fn
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = time_module.sleep if sleep_fn is None else sleep_fn

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fetch_bars(self, ticker: str, target_date: date) -> ProviderFetchResult:
        normalized_ticker = ticker.upper()
        date_str = target_date.isoformat()
        url = f"{BASE_URL}/v2/aggs/ticker/{normalized_ticker}/range/1/minute/{date_str}/{date_str}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self._api_key,
        }

        payload = None
        last_rate_limit_error: HTTPError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                payload = self._request(url, params)
                last_rate_limit_error = None
                break
            except HTTPError as error:
                status_code = error.response.status_code if error.response is not None else None
                if status_code != _RATE_LIMIT_STATUS:
                    raise AppError(f"Failed to fetch intraday bars for '{normalized_ticker}' on {target_date.isoformat()} from Massive: {error}") from error
                last_rate_limit_error = error
                if attempt < self._max_attempts:
                    Logger.warning(
                        f"massive: rate-limited fetching '{normalized_ticker}' on {target_date.isoformat()} "
                        f"(attempt {attempt}/{self._max_attempts}). Retrying in {self._retry_delay_seconds}s.",
                        category=CATEGORY_MASSIVE,
                    )
                    self._sleep(self._retry_delay_seconds)
            except Exception as error:
                raise AppError(f"Failed to fetch intraday bars for '{normalized_ticker}' on {target_date.isoformat()} from Massive: {error}") from error

        if last_rate_limit_error is not None:
            raise AppError(
                f"Failed to fetch intraday bars for '{normalized_ticker}' on {target_date.isoformat()} from Massive "
                f"after {self._max_attempts} attempts (still rate-limited): {last_rate_limit_error}"
            ) from last_rate_limit_error

        if payload.get("status") == "ERROR":
            raise AppError(f"Massive error for '{normalized_ticker}': {payload.get('error', 'unknown error')}")

        raw_bars = payload.get("results")
        if not raw_bars:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        # Pure fetch -- no OHLCV parsing here (croicu/quant-data#56). The raw API response
        # (already sorted ascending by "sort": "asc" above) is archived as-is; quant-stage's
        # massive parser owns turning `results` into OHLCV.
        Logger.info(
            f"quant-ingest: fetched {len(raw_bars)} intraday bars for {normalized_ticker} on {target_date.isoformat()} via Massive.",
            category=CATEGORY_MASSIVE,
        )
        return ProviderFetchResult(payload=payload, payload_kind=PayloadKind.RAW_API_RESPONSE)


def _request(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
