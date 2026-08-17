from __future__ import annotations

from quant_data._internal.shared.providers.ibkr import IBKRIntraDay
from quant_data._internal.shared.providers.massive import MassiveIntraDay
from quant_data._internal.shared.providers.yfinance import YahooFinanceIntraDay


def test_every_provider_declares_a_fetch_version():
    # IntraDayProvider.FETCH_VERSION is read directly off the provider instance by
    # ingest/cli.py's _ingest_one when archiving a fetch (croicu/quant-data#52) -- a provider
    # missing this would fail at runtime, not at type-check time, so this is a cheap guard against
    # that regression for every concrete implementation.
    assert YahooFinanceIntraDay.FETCH_VERSION == "1"
    assert IBKRIntraDay.FETCH_VERSION == "1"
    assert MassiveIntraDay.FETCH_VERSION == "1"
