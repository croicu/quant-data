from __future__ import annotations

import paramiko

# sshtunnel 0.4.0 (its latest PyPI release, last published 2021) unconditionally references
# paramiko.DSSKey while building an internal key-type lookup table used to scan for default keys
# -- paramiko removed DSSKey (DSA key support) entirely in a later major version, since DSA is
# deprecated/insecure. This crashes even when we're not using a DSA key at all (we're ed25519-only
# here), because the lookup table is built eagerly for every key type regardless of which one is
# actually in use. Shimming the attribute (rather than downgrading paramiko to an EOL version with
# since-fixed CVEs) is the standard workaround for this sshtunnel/paramiko version mismatch.
if not hasattr(paramiko, "DSSKey"):
    paramiko.DSSKey = paramiko.RSAKey

from sshtunnel import SSHTunnelForwarder  # noqa: E402

from ..errors import AppError

_SSH_PORT = 22


class SshTunnelTransport:
    """Transport for a Postgres reachable only via SSH port-forward -- today's CroicuWS1 hosting.
    Opens its own SSHTunnelForwarder rather than assuming one is already running externally.
    Key-based auth only, one tunnel per instance (matching PostgresDatabase's own
    single-connection-per-invocation lifecycle)."""

    def __init__(self, host: str, port: int, ssh_user: str, ssh_key_path: str) -> None:
        self._host = host
        self._port = port
        self._ssh_user = ssh_user
        self._ssh_key_path = ssh_key_path
        self._tunnel: SSHTunnelForwarder | None = None

    def open(self) -> tuple[str, int]:
        try:
            tunnel = SSHTunnelForwarder(
                (self._host, _SSH_PORT),
                ssh_username=self._ssh_user,
                ssh_pkey=self._ssh_key_path,
                remote_bind_address=("localhost", self._port),
            )
            tunnel.start()
        except Exception as error:
            raise AppError(f"Failed to open SSH tunnel to {self._host} as {self._ssh_user} (key: {self._ssh_key_path}): {error}") from error

        self._tunnel = tunnel
        return "localhost", tunnel.local_bind_port

    def close(self) -> None:
        if self._tunnel is not None:
            self._tunnel.stop()
