from __future__ import annotations

import time
from datetime import date, datetime, timezone
from datetime import time as time_type
from zoneinfo import ZoneInfo

from ib_async import IB, StartupFetchNONE, Stock

from quant_data.protocols import OHLCV

from ..diagnostics import Logger
from ..errors import AppError

CATEGORY_IBKR = "ibkr"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002  # IB Gateway paper-trading default; live Gateway/TWS use different ports.
DEFAULT_CLIENT_ID = 1  # Fine as a fixed default: matches this repo's single-writer ingest model.

_SESSION_CLOSE = time_type(20, 0)  # 20:00 ET covers regular + extended hours for the session date.
_EASTERN = ZoneInfo("America/New_York")


class IBKRIntraDay:
    """IntraDayProvider backed by IB Gateway/TWS via ib_async.

    Unlike YahooFinanceIntraDay's per-call HTTP fetch, IBKR's connection handshake is expensive
    enough (and a Read-Only-API Gateway's default startup fetch of positions/orders/account
    updates costly enough -- see StartupFetchNONE below) that it's meant to be amortized across a
    batch: call connect() once before a run of fetch_bars() calls, close() when done.
    """

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

    def fetch_bars(self, ticker: str, target_date: date) -> list[OHLCV]:
        if self._ib is None:
            raise AppError("IBKRIntraDay.fetch_bars called before connect() -- call connect() once per batch before fetching.")

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
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
            )
        except Exception as error:
            raise AppError(f"Failed to fetch IBKR intraday bars for '{normalized_ticker}' on {target_date.isoformat()}: {error}") from error

        if not raw_bars:
            raise AppError(f"No data available for '{normalized_ticker}' on {target_date.isoformat()}.")

        bars: list[OHLCV] = []
        for raw_bar in raw_bars:
            timestamp_utc = raw_bar.date
            if timestamp_utc.tzinfo is None:
                timestamp_utc = timestamp_utc.replace(tzinfo=timezone.utc)
            else:
                timestamp_utc = timestamp_utc.astimezone(timezone.utc)

            bar = OHLCV(
                ticker=normalized_ticker,
                timestamp=timestamp_utc,
                open=float(raw_bar.open),
                high=float(raw_bar.high),
                low=float(raw_bar.low),
                close=float(raw_bar.close),
                volume=int(raw_bar.volume),
                # Unlike Yahoo, IBKR only returns bars it actually has trade data for -- no
                # synthetic/NaN placeholder rows -- so no zero-volume-as-incomplete heuristic
                # applies here; a zero-volume bar is a real "no trades that minute" fact.
                incomplete=False,
            )
            bars.append(bar)

        Logger.info(
            f"quant-ingest: fetched {len(bars)} intraday bars for {normalized_ticker} on {target_date.isoformat()} via IBKR.",
            category=CATEGORY_IBKR,
        )
        return bars
