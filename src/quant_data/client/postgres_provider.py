from __future__ import annotations

from quant_data._internal.contracts import MarketDataProvider
from quant_data._internal.shared.postgres import PostgresDatabase


def create_postgres_provider(*, host: str, port: int, dbname: str, user: str = "quant_reader", password: str = "") -> MarketDataProvider:
    return PostgresDatabase(host=host, port=port, user=user, password=password, dbname=dbname)
