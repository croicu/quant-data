from __future__ import annotations

from unittest.mock import patch

from quant_data.client.postgres_provider import create_postgres_provider


class _FakeLogger:
    """Constructor-injected fake standing in for a host's own LoggingSink (quant-data#20)."""

    def __init__(self) -> None:
        self.perf_calls: list[str] = []

    def diagnostic(self, message: str, category: str = "general") -> None:
        pass

    def info(self, message: str, category: str = "general") -> None:
        pass

    def warning(self, message: str, category: str = "general") -> None:
        pass

    def error(self, message: str, category: str = "general") -> None:
        pass

    def fatal(self, message: str, category: str = "general") -> None:
        pass

    def perf(self, description: str, elapsed_seconds: float) -> None:
        self.perf_calls.append(description)


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connects_as_quant_reader_by_default(mock_psycopg):
    create_postgres_provider(host="localhost", port=5433, dbname="quant_data")

    # "localhost" gets normalized to the literal IPv4 address before reaching psycopg -- see
    # quant-data#19 (psycopg/libpq's dual-stack resolution of "localhost" cost ~130s in practice).
    mock_psycopg.connect.assert_called_once_with(host="127.0.0.1", port=5433, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC")


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connects_using_host_as_given_when_not_localhost(mock_psycopg):
    create_postgres_provider(host="db.example.com", port=5433, dbname="quant_data")

    mock_psycopg.connect.assert_called_once_with(
        host="db.example.com", port=5433, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC"
    )


@patch("quant_data._internal.shared.transports.ssh_tunnel.SSHTunnelForwarder")
@patch("quant_data._internal.shared.postgres.psycopg")
def test_uses_ssh_tunnel_transport_when_ssh_settings_given(mock_psycopg, mock_forwarder_cls):
    mock_tunnel = mock_forwarder_cls.return_value
    mock_tunnel.local_bind_port = 54321

    create_postgres_provider(host="box.example.com", port=5432, dbname="quant_data", ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")

    mock_forwarder_cls.assert_called_once_with(
        ("box.example.com", 22),
        ssh_username="alex",
        ssh_pkey="/home/alex/.ssh/id_ed25519",
        remote_bind_address=("localhost", 5432),
        local_bind_address=("127.0.0.1", 0),
    )
    mock_tunnel.start.assert_called_once()
    mock_psycopg.connect.assert_called_once_with(host="127.0.0.1", port=54321, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC")


@patch("quant_data._internal.shared.postgres.psycopg")
def test_forwards_injected_logger_to_postgres_database(mock_psycopg):
    logger = _FakeLogger()

    create_postgres_provider(host="localhost", port=5433, dbname="quant_data", logger=logger)

    assert "transport.open()" in logger.perf_calls
    assert "psycopg.connect()" in logger.perf_calls
