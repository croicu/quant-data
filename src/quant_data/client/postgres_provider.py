from __future__ import annotations

from quant_data._internal.contracts import MarketDataProvider
from quant_data._internal.shared.diagnostics import Logger
from quant_data._internal.shared.postgres import PostgresDatabase
from quant_data._internal.shared.transports import resolve_transport
from quant_data.protocols import LoggingSink


def create_postgres_provider(
    *,
    host: str,
    port: int,
    dbname: str,
    user: str = "quant_reader",
    password: str = "",
    ssh_user: str | None = None,
    ssh_key_path: str | None = None,
    logger: LoggingSink = Logger,
) -> MarketDataProvider:
    transport = resolve_transport(host=host, port=port, ssh_user=ssh_user, ssh_key_path=ssh_key_path)
    return PostgresDatabase(transport=transport, user=user, password=password, dbname=dbname, logger=logger)
