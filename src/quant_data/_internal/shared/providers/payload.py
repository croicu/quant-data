from __future__ import annotations

from quant_data.protocols import OHLCV


def parsed_bars_payload(bars: list[OHLCV]) -> dict:
    """JSON-serializable form of already-parsed OHLCV bars, for providers with no genuine raw
    payload for quant_ingest to archive (PayloadKind.PARSED_BARS -- see
    quant_data._internal.contracts.ProviderFetchResult)."""
    serialized_bars: list[dict] = []
    for bar in bars:
        serialized_bars.append(
            {
                "ticker": bar.ticker,
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "data_quality": bar.data_quality.value,
            }
        )
    return {"bars": serialized_bars}
