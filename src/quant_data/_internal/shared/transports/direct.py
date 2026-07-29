from __future__ import annotations


class DirectTransport:
    """No-op transport for a Postgres already reachable at host:port -- a cloud-hosted database
    (e.g. RDS, Azure Database for PostgreSQL) or a manually pre-established tunnel. Keeps
    PostgresDatabase transport-agnostic: this is exactly what it did implicitly before
    ConnectionTransport existed."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def open(self) -> tuple[str, int]:
        return self._host, self._port

    def close(self) -> None:
        pass
