from __future__ import annotations

import time
from datetime import date, datetime, timezone
from datetime import time as time_type
from zoneinfo import ZoneInfo

from ib_async import IB, StartupFetchNONE, Stock

from quant_data._internal.contracts import DEFAULT_METHODS_BY_PROVIDER, PayloadKind, ProviderFetchResult

from ..diagnostics import Logger
from ..errors import AppError
from .payload import raw_bars_payload

CATEGORY_IBKR = "ibkr"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002  # IB Gateway paper-trading default; live Gateway/TWS use different ports.
DEFAULT_CLIENT_ID = 1  # Fine as a fixed default: matches this repo's single-writer ingest model.

_SESSION_CLOSE = time_type(20, 0)  # 20:00 ET covers regular + extended hours for the session date.
_EASTERN = ZoneInfo("America/New_York")


def _bar_timestamp_utc(raw_bar) -> datetime:
    timestamp_utc = raw_bar.date
    if timestamp_utc.tzinfo is None:
        return timestamp_utc.replace(tzinfo=timezone.utc)
    return timestamp_utc.astimezone(timezone.utc)


def _serialize_trades_bar(raw_bar) -> dict:
    return {
        "timestamp": _bar_timestamp_utc(raw_bar).isoformat(),
        "open": float(raw_bar.open),
        "high": float(raw_bar.high),
        "low": float(raw_bar.low),
        "close": float(raw_bar.close),
        "volume": int(raw_bar.volume),
    }


def _serialize_bid_ask_bar(raw_bar) -> dict:
    # BID_ASK bars are quote bars, not trade bars -- .open/.close are the time-averaged bid/ask,
    # not a real OHLC open/close, so they're kept under honest field names rather than borrowing
    # TRADES' OHLCV vocabulary. .volume/.average/.barCount come back as -1 (not applicable to a
    # quote-type bar, confirmed live -- tasks/ingestion_variable_inventory.md Sec 1.3), so they're
    # omitted entirely rather than archived as meaningless placeholder values.
    return {
        "timestamp": _bar_timestamp_utc(raw_bar).isoformat(),
        "avg_bid": float(raw_bar.open),
        "avg_ask": float(raw_bar.close),
        "high": float(raw_bar.high),
        "low": float(raw_bar.low),
    }


def _serialize_midpoint_bar(raw_bar) -> dict:
    # Unlike BID_ASK, MIDPOINT bars are a genuine OHLC series -- .open/.high/.low/.close really are
    # the open/high/low/close of the bid/ask midpoint price over the bar (not a flat time-average),
    # so they keep the same OHLC field names as TRADES rather than needing honest renaming.
    # .volume/.average/.barCount still come back -1 on this quote-type bar (confirmed live --
    # tasks/ingestion_variable_inventory.md Sec 1.4), so they're omitted, same as BID_ASK.
    return {
        "timestamp": _bar_timestamp_utc(raw_bar).isoformat(),
        "open": float(raw_bar.open),
        "high": float(raw_bar.high),
        "low": float(raw_bar.low),
        "close": float(raw_bar.close),
    }


_SERIALIZERS = {
    "TRADES": _serialize_trades_bar,
    "BID_ASK": _serialize_bid_ask_bar,
    "MIDPOINT": _serialize_midpoint_bar,
}


class IBKRIntraDay:
    """IntraDayProvider backed by IB Gateway/TWS via ib_async.

    Unlike YahooFinanceIntraDay's per-call HTTP fetch, IBKR's connection handshake is expensive
    enough (and a Read-Only-API Gateway's default startup fetch of positions/orders/account
    updates costly enough -- see StartupFetchNONE below) that it's meant to be amortized across a
    batch: call connect() once before a run of fetch_bars() calls, close() when done.
    """

    FETCH_VERSION = "1"
    DEFAULT_METHODS = DEFAULT_METHODS_BY_PROVIDER["ibkr"]

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        client_id: int = DEFAULT_CLIENT_ID,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib: IB | None = None

    def connect(self) -> None:
        if self._ib is not None:
            return

        ib = IB()
        start = time.monotonic()
        try:
            # fetchFields=StartupFetchNONE skips connect()'s default positions/orders/account
            # fetch entirely -- a Read-Only-API Gateway (the right setting for data-only ingest)
            # rejects that fetch, which otherwise costs ~10s per connection waiting for a timeout.
            ib.connect(self._host, self._port, clientId=self._client_id, fetchFields=StartupFetchNONE)
        except Exception as error:
            raise AppError(f"Failed to connect to IBKR at {self._host}:{self._port}: {error}") from error
        Logger.perf(f"IBKR connect ({self._host}:{self._port})", time.monotonic() - start)
        self._ib = ib

    def close(self) -> None:
        if self._ib is None:
            return
        self._ib.disconnect()
        self._ib = None

    def fetch_bars(self, ticker: str, target_date: date, method: str | None = None) -> ProviderFetchResult:
        # Pure fetch -- no OHLCV parsing here (croicu/quant-data#56).
        if self._ib is None:
            raise AppError("IBKRIntraDay.fetch_bars called before connect() -- call connect() once per batch before fetching.")

        effective_method = method if method is not None else self.DEFAULT_METHODS[0]
        if effective_method not in _SERIALIZERS:
            raise AppError(f"IBKRIntraDay does not recognize method '{effective_method}' -- expected one of: {list(_SERIALIZERS)}.")
        serialize_bar = _SERIALIZERS[effective_method]

        normalized_ticker = ticker.upper()
        contract = Stock(normalized_ticker, "SMART", "USD")
        try:
            qualified = self._ib.qualifyContracts(contract)
        except Exception as error:
            raise AppError(f"Failed to qualify IBKR contract for '{normalized_ticker}': {error}") from error
        if not qualified:
            raise AppError(f"IBKR could not resolve a contract for '{normalized_ticker}'.")

        end_datetime = datetime.combine(target_date, _SESSION_CLOSE, tzinfo=_EASTERN)
        try:
            raw_bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=end_datetime,
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow=effective_method,
                useRTH=False,
                formatDate=2,
            )
        except Exception as error:
            raise AppError(
                f"Failed to fetch IBKR intraday bars for '{normalized_ticker}' on {target_date.isoformat()} (method={effective_method}): {error}"
            ) from error

        if not raw_bars:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()} (method={effective_method}).")

        serialized_bars: list[dict] = []
        for raw_bar in raw_bars:
            serialized_bars.append(serialize_bar(raw_bar))

        Logger.info(
            f"quant-ingest: fetched {len(serialized_bars)} intraday bars for {normalized_ticker} on {target_date.isoformat()} "
            f"via IBKR (method={effective_method}).",
            category=CATEGORY_IBKR,
        )
        return ProviderFetchResult(payload=raw_bars_payload(serialized_bars), payload_kind=PayloadKind.PARSED_BARS, method=effective_method)
