from __future__ import annotations

from unittest.mock import patch

import pytest

from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.transports import resolve_transport
from quant_data._internal.shared.transports.direct import DirectTransport
from quant_data._internal.shared.transports.ssh_tunnel import SshTunnelTransport


def test_resolve_transport_returns_direct_when_ssh_settings_absent():
    transport = resolve_transport(host="localhost", port=5433, ssh_user=None, ssh_key_path=None)

    assert isinstance(transport, DirectTransport)


def test_resolve_transport_returns_ssh_tunnel_when_ssh_settings_present():
    transport = resolve_transport(host="box.example.com", port=5432, ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")

    assert isinstance(transport, SshTunnelTransport)


def test_direct_transport_open_returns_configured_host_and_port():
    transport = DirectTransport(host="db.example.com", port=5432)

    assert transport.open() == ("db.example.com", 5432)


def test_direct_transport_close_is_a_no_op():
    transport = DirectTransport(host="db.example.com", port=5432)

    transport.close()  # must not raise


@patch("quant_data._internal.shared.transports.ssh_tunnel.SSHTunnelForwarder")
def test_ssh_tunnel_transport_open_returns_local_forwarded_port(mock_forwarder_cls):
    mock_tunnel = mock_forwarder_cls.return_value
    mock_tunnel.local_bind_port = 54321

    transport = SshTunnelTransport(host="box.example.com", port=5432, ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")
    host, port = transport.open()

    mock_forwarder_cls.assert_called_once_with(
        ("box.example.com", 22),
        ssh_username="alex",
        ssh_pkey="/home/alex/.ssh/id_ed25519",
        remote_bind_address=("localhost", 5432),
        local_bind_address=("127.0.0.1", 0),
    )
    mock_tunnel.start.assert_called_once()
    # Must be the literal IPv4 address, not "localhost" -- psycopg/libpq resolves the bare
    # hostname as dual-stack and can fall back from an unreachable IPv6 loopback with a ~130s
    # internal timeout instead of connecting immediately (quant-data#19).
    assert (host, port) == ("127.0.0.1", 54321)


@patch("quant_data._internal.shared.transports.ssh_tunnel.SSHTunnelForwarder")
def test_ssh_tunnel_transport_wraps_start_failure_in_app_error(mock_forwarder_cls):
    mock_tunnel = mock_forwarder_cls.return_value
    mock_tunnel.start.side_effect = Exception("auth failed")

    transport = SshTunnelTransport(host="box.example.com", port=5432, ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")

    with pytest.raises(AppError):
        transport.open()


@patch("quant_data._internal.shared.transports.ssh_tunnel.SSHTunnelForwarder")
def test_ssh_tunnel_transport_close_stops_the_tunnel(mock_forwarder_cls):
    mock_tunnel = mock_forwarder_cls.return_value
    mock_tunnel.local_bind_port = 54321

    transport = SshTunnelTransport(host="box.example.com", port=5432, ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")
    transport.open()
    transport.close()

    mock_tunnel.stop.assert_called_once()


@patch("quant_data._internal.shared.transports.ssh_tunnel.SSHTunnelForwarder")
def test_ssh_tunnel_transport_close_before_open_does_not_raise(mock_forwarder_cls):
    transport = SshTunnelTransport(host="box.example.com", port=5432, ssh_user="alex", ssh_key_path="/home/alex/.ssh/id_ed25519")

    transport.close()  # must not raise
