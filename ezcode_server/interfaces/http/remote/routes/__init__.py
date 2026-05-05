"""Route handler mixins for the remote HTTP control plane."""

from ezcode_server.interfaces.http.remote.routes.admin import RemoteAdminRoutes
from ezcode_server.interfaces.http.remote.routes.artifacts import RemoteArtifactRoutes
from ezcode_server.interfaces.http.remote.routes.base import RemoteRelayBaseHandler
from ezcode_server.interfaces.http.remote.routes.chat import RemoteChatRoutes
from ezcode_server.interfaces.http.remote.routes.collaboration import RemoteCollaborationRoutes
from ezcode_server.interfaces.http.remote.routes.manifests import RemoteManifestRoutes
from ezcode_server.interfaces.http.remote.routes.peer import RemotePeerRoutes
from ezcode_server.interfaces.http.remote.routes.runtime import RemoteRuntimeRoutes
from ezcode_server.interfaces.http.remote.routes.sessions import RemoteSessionRoutes
from ezcode_server.interfaces.http.remote.routes.taskflow import RemoteTaskflowRoutes

__all__ = [
    "RemoteAdminRoutes",
    "RemoteArtifactRoutes",
    "RemoteChatRoutes",
    "RemoteCollaborationRoutes",
    "RemoteManifestRoutes",
    "RemotePeerRoutes",
    "RemoteRelayBaseHandler",
    "RemoteRuntimeRoutes",
    "RemoteSessionRoutes",
    "RemoteTaskflowRoutes",
]
