from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas
import yfinance

from quant_data._internal.contracts import DEFAULT_METHODS_BY_PROVIDER, PayloadKind, ProviderFetchResult

from ..diagnostics import Logger
from ..errors import AppError
from .payload import raw_bars_payload
from .yfinance_logging import CATEGORY_YFINANCE, install_log_capture

install_log_capture()


def _safe_float_or_none(value: float) -> float | None:
    if pandas.isna(value):
        return None
    return float(value)


def _safe_volume_or_none(value: float) -> int | None:
    if pandas.isna(value):
        return None
    return int(value)


class YahooFinanceIntraDay:
    FETCH_VERSION = "1"
    DEFAULT_METHODS = DEFAULT_METHODS_BY_PROVIDER["yfinance"]

    def connect(self) -> None:
        # Stateless per-call HTTP fetch -- no persistent connection to establish, unlike
        # IBKRIntraDay. Satisfies IntraDayProvider's connect()/close() lifecycle as a no-op so
        # ingest can treat every provider uniformly.
        pass

    def close(self) -> None:
        pass

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        # Pure fetch -- no OHLCV parsing here (croicu/quant-data#56). NaN values are preserved as
        # JSON null (not coerced to 0.0), so quant-stage's yfinance parser can make its own
        # incomplete/data-quality determination from the genuine raw signal, not a value this repo
        # already interpreted at fetch time.
        normalized_ticker = ticker.upper()
        effective_method = method if method is not None else self.DEFAULT_METHODS[0]
        if effective_method not in self.DEFAULT_METHODS:
            raise AppError(f"YahooFinanceIntraDay does not recognize method '{effective_method}' -- expected one of: {self.DEFAULT_METHODS}.")
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)

        try:
            history = yfinance.Ticker(normalized_ticker).history(start=start, end=end, interval="1m", prepost=True)
        except Exception as error:
            raise AppError(f"Failed to fetch intraday bars for '{normalized_ticker}' on {target_date.isoformat()}: {error}") from error

        if history.empty:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        raw_bars: list[dict] = []
        for row_timestamp, row in history.iterrows():
            timestamp_utc = row_timestamp.tz_convert("UTC").to_pydatetime()
            raw_bars.append(
                {
                    "timestamp": timestamp_utc.isoformat(),
                    "open": _safe_float_or_none(row["Open"]),
                    "high": _safe_float_or_none(row["High"]),
                    "low": _safe_float_or_none(row["Low"]),
                    "close": _safe_float_or_none(row["Close"]),
                    "volume": _safe_volume_or_none(row["Volume"]),
                }
            )

        Logger.info(
            f"quant-ingest: fetched {len(raw_bars)} intraday bars for {normalized_ticker} on {target_date.isoformat()}.",
            category=CATEGORY_YFINANCE,
        )
        return ProviderFetchResult(payload=raw_bars_payload(raw_bars), payload_kind=PayloadKind.PARSED_BARS, method=effective_method)
