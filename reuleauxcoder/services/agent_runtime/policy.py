"""Agent runtime capability and MCP policy helpers."""

from __future__ import annotations

from typing import Any


def _server_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        servers = value.get("servers", {})
        if isinstance(servers, dict):
            return [str(name) for name in servers.keys()]
        if isinstance(servers, list):
            return [str(name) for name in servers]
    if isinstance(value, list):
        return [str(name) for name in value]
    return []


class PlatformMCPPolicy:
    """Render MCP config from platform inventory and Agent allowlist."""

    def __init__(
        self,
        *,
        platform_servers: dict[str, dict[str, Any]],
        allowed_servers: list[str],
    ) -> None:
        self.platform_servers = dict(platform_servers)
        self.allowed_servers = [str(server) for server in allowed_servers]

    def render_for_agent(self, agent_mcp: dict[str, Any] | None) -> dict[str, Any]:
        requested = _server_names(agent_mcp or {})
        effective: dict[str, Any] = {}
        for server in requested:
            if server not in self.allowed_servers:
                continue
            if server not in self.platform_servers:
                continue
            effective[server] = dict(self.platform_servers[server])
        return {"servers": effective}


class AgentCapabilityPolicy:
    """Check whether an Agent may use a platform capability."""

    def __init__(
        self,
        *,
        platform_capabilities: list[str],
        agent_capabilities: list[str],
    ) -> None:
        self.platform_capabilities = set(platform_capabilities)
        self.agent_capabilities = set(agent_capabilities)

    def allows(self, capability: str) -> bool:
        return (
            capability in self.platform_capabilities
            and capability in self.agent_capabilities
        )

    def explain_denial(self, capability: str) -> str:
        if capability not in self.platform_capabilities:
            return "capability not available on platform"
        if capability not in self.agent_capabilities:
            return "capability not granted to agent"
        return "capability allowed"
