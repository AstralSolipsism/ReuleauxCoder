"""Server-side remote relay primitives."""

from ezcode_server.relay.auth import TokenManager
from ezcode_server.relay.cleanup import cleanup_all_peers, request_peer_cleanup
from ezcode_server.relay.errors import (
    AuthError,
    PeerDisconnectedError,
    PeerNotFoundError,
    RegisterRejectedError,
    RemoteExecError,
    RemoteTimeoutError,
    RemoteToolError,
)
from ezcode_server.relay.peer_registry import PeerInfo, PeerRegistry
from ezcode_server.relay.server import RelayServer

__all__ = [
    "AuthError",
    "PeerDisconnectedError",
    "PeerInfo",
    "PeerNotFoundError",
    "PeerRegistry",
    "RegisterRejectedError",
    "RelayServer",
    "RemoteExecError",
    "RemoteTimeoutError",
    "RemoteToolError",
    "TokenManager",
    "cleanup_all_peers",
    "request_peer_cleanup",
]
