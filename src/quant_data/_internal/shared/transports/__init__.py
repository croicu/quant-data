from __future__ import annotations

from quant_data._internal.contracts import ConnectionTransport

from .direct import DirectTransport
from .ssh_tunnel import SshTunnelTransport


def resolve_transport(host: str, port: int, ssh_user: str | None, ssh_key_path: str | None) -> ConnectionTransport:
    if ssh_user is not None and ssh_key_path is not None:
        return SshTunnelTransport(host=host, port=port, ssh_user=ssh_user, ssh_key_path=ssh_key_path)
    return DirectTransport(host=host, port=port)
