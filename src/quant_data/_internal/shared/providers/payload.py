from __future__ import annotations


def raw_bars_payload(bars: list[dict]) -> dict:
    """JSON-serializable wrapper around a provider's own raw per-bar dicts, for archiving payloads
    with no genuine raw API response to store (PayloadKind.PARSED_BARS -- see
    quant_data._internal.contracts.ProviderFetchResult). Each provider builds its own bar dicts,
    since the raw field shape differs per provider; this just standardizes the top-level wrapper
    quant-stage's parsers read back out (croicu/quant-data#56)."""
    return {"bars": bars}
