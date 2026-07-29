from __future__ import annotations

from unittest.mock import patch

from quant_data.client.postgres_provider import create_postgres_provider


@patch("quant_data._internal.shared.postgres.psycopg")
def test_connects_as_quant_reader_by_default(mock_psycopg):
    create_postgres_provider(host="localhost", port=5433, dbname="quant_data")

    mock_psycopg.connect.assert_called_once_with(host="localhost", port=5433, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC")


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
    )
    mock_tunnel.start.assert_called_once()
    mock_psycopg.connect.assert_called_once_with(host="localhost", port=54321, user="quant_reader", password="", dbname="quant_data", options="-c TimeZone=UTC")
