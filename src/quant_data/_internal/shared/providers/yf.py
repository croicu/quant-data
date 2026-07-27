from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas
import yfinance

from quant_data.protocols import OHLCV

from ..diagnostics import Logger
from ..errors import AppError

CATEGORY_INTRADAY_FETCH = "intraday_fetch"


def _safe_float(value: float) -> tuple[float, bool]:
    if pandas.isna(value):
        return 0.0, True
    return float(value), False


def _safe_int(value: float) -> tuple[int, bool]:
    if pandas.isna(value):
        return 0, True
    return int(value), False


class YahooFinanceIntraDay:
    def fetch_bars(self, ticker: str, target_date: date) -> list[OHLCV]:
        normalized_ticker = ticker.upper()
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)

        try:
            history = yfinance.Ticker(normalized_ticker).history(start=start, end=end, interval="1m", prepost=True)
        except Exception as error:
            raise AppError(f"Failed to fetch intraday bars for '{normalized_ticker}' on {target_date.isoformat()}: {error}") from error

        if history.empty:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        bars: list[OHLCV] = []
        for row_timestamp, row in history.iterrows():
            timestamp_utc = row_timestamp.tz_convert("UTC").to_pydatetime()

            open_value, open_incomplete = _safe_float(row["Open"])
            high_value, high_incomplete = _safe_float(row["High"])
            low_value, low_incomplete = _safe_float(row["Low"])
            close_value, close_incomplete = _safe_float(row["Close"])
            volume_value, volume_incomplete = _safe_int(row["Volume"])
            if volume_value == 0:
                # A real tick can't have zero volume — yfinance reports both genuine data gaps
                # (NaN, handled above) and "no trade data" (a literal 0) for the same underlying
                # problem, most often pre-market/after-hours minutes with no trades recorded.
                volume_incomplete = True

            incomplete = open_incomplete or high_incomplete or low_incomplete or close_incomplete or volume_incomplete

            bar = OHLCV(
                ticker=normalized_ticker,
                timestamp=timestamp_utc,
                open=open_value,
                high=high_value,
                low=low_value,
                close=close_value,
                volume=volume_value,
                incomplete=incomplete,
            )
            bars.append(bar)

        Logger.info(
            f"quant-ingest: fetched {len(bars)} intraday bars for {normalized_ticker} on {target_date.isoformat()}.",
            category=CATEGORY_INTRADAY_FETCH,
        )
        return bars
