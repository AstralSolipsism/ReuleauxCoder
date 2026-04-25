"""Tool wrappers for MCP servers running on remote peers."""

from __future__ import annotations

from typing import Any

from reuleauxcoder.extensions.remote_exec.backend import RemoteRelayToolBackend
from reuleauxcoder.extensions.remote_exec.protocol import RemoteMCPToolInfo
from reuleauxcoder.extensions.tools.base import Tool


class RemotePeerMCPTool(Tool):
    """Expose a peer-hosted MCP tool through the remote relay."""

    tool_source = "mcp"

    def __init__(self, backend: RemoteRelayToolBackend, tool_info: RemoteMCPToolInfo):
        super().__init__(backend)
        self._tool_info = tool_info
        self.name = tool_info.name
        self.description = tool_info.description
        self.parameters = tool_info.input_schema or {"type": "object", "properties": {}}
        self.server_name = tool_info.server_name

    def execute(self, **kwargs: Any) -> str:
        if not isinstance(self.backend, RemoteRelayToolBackend):
            return "Error: peer MCP tool requires a remote relay backend"
        return self.backend.exec_tool(
            "mcp",
            {
                "server_name": self.server_name,
                "tool_name": self._tool_info.name,
                "arguments": kwargs,
            },
        )
