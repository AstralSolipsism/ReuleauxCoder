"""Remote execution relay protocol message models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RelayEnvelope:
    """Top-level message wrapper for all relay communications."""

    type: str
    request_id: str | None = None
    peer_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "request_id": self.request_id,
            "peer_id": self.peer_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelayEnvelope":
        return cls(
            type=d["type"],
            request_id=d.get("request_id"),
            peer_id=d.get("peer_id"),
            payload=d.get("payload", {}),
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@dataclass
class RegisterRequest:
    bootstrap_token: str
    host_info_min: dict[str, Any] = field(default_factory=dict)
    cwd: str = "."
    workspace_root: str | None = None
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bootstrap_token": self.bootstrap_token,
            "host_info_min": self.host_info_min,
            "cwd": self.cwd,
            "workspace_root": self.workspace_root,
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegisterRequest":
        return cls(
            bootstrap_token=d["bootstrap_token"],
            host_info_min=d.get("host_info_min", {}),
            cwd=d.get("cwd", "."),
            workspace_root=d.get("workspace_root"),
            capabilities=d.get("capabilities", []),
        )


@dataclass
class RegisterResponse:
    peer_id: str
    peer_token: str
    heartbeat_interval_sec: int = 10

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "peer_token": self.peer_token,
            "heartbeat_interval_sec": self.heartbeat_interval_sec,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegisterResponse":
        return cls(
            peer_id=d["peer_id"],
            peer_token=d["peer_token"],
            heartbeat_interval_sec=d.get("heartbeat_interval_sec", 10),
        )


@dataclass
class RegisterRejected:
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegisterRejected":
        return cls(reason=d.get("reason", "unknown"))


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@dataclass
class Heartbeat:
    peer_token: str
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"peer_token": self.peer_token, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Heartbeat":
        return cls(peer_token=d["peer_token"], ts=d.get("ts", 0.0))


# ---------------------------------------------------------------------------
# Peer MCP manifest and tool reports
# ---------------------------------------------------------------------------


@dataclass
class MCPArtifactManifest:
    platform: str
    path: str
    sha256: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "path": self.path,
            "sha256": self.sha256,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPArtifactManifest":
        return cls(
            platform=str(d.get("platform", "")),
            path=str(d.get("path", "")),
            sha256=str(d.get("sha256", "")),
            url=str(d.get("url", "")),
        )


@dataclass
class MCPLaunchManifest:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPLaunchManifest":
        raw_args = d.get("args", [])
        raw_env = d.get("env", {})
        return cls(
            command=str(d.get("command", "")),
            args=[str(arg) for arg in raw_args] if isinstance(raw_args, list) else [],
            env=(
                {str(k): str(v) for k, v in raw_env.items()}
                if isinstance(raw_env, dict)
                else {}
            ),
            cwd=str(d["cwd"]) if d.get("cwd") is not None else None,
        )


@dataclass
class MCPServerManifest:
    name: str = ""
    version: str = ""
    distribution: str = "artifact"
    artifact: MCPArtifactManifest | None = None
    launch: MCPLaunchManifest = field(
        default_factory=lambda: MCPLaunchManifest(command="")
    )
    permissions: dict[str, Any] = field(default_factory=dict)
    requirements: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "distribution": self.distribution,
            "artifact": self.artifact.to_dict() if self.artifact is not None else None,
            "launch": self.launch.to_dict(),
            "permissions": self.permissions,
            "requirements": self.requirements,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPServerManifest":
        raw_artifact = d.get("artifact")
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "")),
            distribution=str(d.get("distribution", "artifact") or "artifact"),
            artifact=(
                MCPArtifactManifest.from_dict(raw_artifact)
                if isinstance(raw_artifact, dict)
                else None
            ),
            launch=MCPLaunchManifest.from_dict(d.get("launch", {})),
            permissions=(
                dict(d.get("permissions", {}))
                if isinstance(d.get("permissions", {}), dict)
                else {}
            ),
            requirements=(
                {str(k): str(v) for k, v in d.get("requirements", {}).items()}
                if isinstance(d.get("requirements", {}), dict)
                else {}
            ),
        )


@dataclass
class MCPManifestRequest:
    peer_token: str
    os: str
    arch: str
    workspace: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "os": self.os,
            "arch": self.arch,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPManifestRequest":
        return cls(
            peer_token=d["peer_token"],
            os=str(d.get("os", "")),
            arch=str(d.get("arch", "")),
            workspace=str(d.get("workspace", "")),
        )


@dataclass
class MCPManifestResponse:
    servers: list[MCPServerManifest] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "servers": [server.to_dict() for server in self.servers],
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPManifestResponse":
        return cls(
            servers=[
                MCPServerManifest.from_dict(item)
                for item in d.get("servers", [])
                if isinstance(item, dict)
            ],
            diagnostics=[
                dict(item)
                for item in d.get("diagnostics", [])
                if isinstance(item, dict)
            ],
        )


@dataclass
class RemoteMCPToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "server_name": self.server_name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RemoteMCPToolInfo":
        input_schema = d.get("input_schema", {})
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        return cls(
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            input_schema=input_schema,
            server_name=str(d.get("server_name", "")),
        )


@dataclass
class PeerMCPToolsReport:
    peer_token: str
    tools: list[RemoteMCPToolInfo] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "tools": [tool.to_dict() for tool in self.tools],
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PeerMCPToolsReport":
        return cls(
            peer_token=d["peer_token"],
            tools=[
                RemoteMCPToolInfo.from_dict(item)
                for item in d.get("tools", [])
                if isinstance(item, dict)
            ],
            diagnostics=[
                dict(item)
                for item in d.get("diagnostics", [])
                if isinstance(item, dict)
            ],
        )


# ---------------------------------------------------------------------------
# Lightweight CLI environment manifest
# ---------------------------------------------------------------------------


def _bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _string_list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _docs_value(value: Any) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    if not isinstance(value, list):
        return docs
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue
        docs.append({"title": title, "url": url})
    return docs


def _string_dict_list_value(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = {
            str(key): str(val).strip()
            for key, val in item.items()
            if val is not None and str(val).strip()
        }
        if normalized:
            items.append(normalized)
    return items


@dataclass
class EnvironmentCLIToolManifest:
    name: str
    command: str = ""
    enabled: bool = True
    placement: str = "local"
    capabilities: list[str] = field(default_factory=list)
    requirements: dict[str, str] = field(default_factory=dict)
    check: str = ""
    install: str = ""
    version: str | None = None
    source: str = ""
    description: str = ""
    repo_url: str = ""
    docs: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    install_prompt: str = ""
    verify_prompt: str = ""
    notes: list[str] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)
    risk_level: str = ""
    last_action: str = ""
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "enabled": self.enabled,
            "placement": self.placement,
            "capabilities": self.capabilities,
            "requirements": dict(self.requirements),
            "check": self.check,
            "install": self.install,
            "source": self.source,
            "description": self.description,
            "repo_url": self.repo_url,
            "docs": [dict(item) for item in self.docs],
            "evidence": [dict(item) for item in self.evidence],
            "install_prompt": self.install_prompt,
            "verify_prompt": self.verify_prompt,
            "notes": list(self.notes),
            "credentials": list(self.credentials),
            "risk_level": self.risk_level,
            "last_action": self.last_action,
            "last_updated": self.last_updated,
        }
        if self.version is not None:
            data["version"] = self.version
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentCLIToolManifest":
        raw_capabilities = d.get("capabilities", [])
        raw_requirements = d.get("requirements", {})
        return cls(
            name=str(d.get("name", "")),
            command=str(d.get("command", "")),
            enabled=_bool_value(d.get("enabled", True)),
            placement=str(d.get("placement", "local") or "local"),
            capabilities=(
                [str(item) for item in raw_capabilities]
                if isinstance(raw_capabilities, list)
                else []
            ),
            requirements=(
                {str(k): str(v) for k, v in raw_requirements.items()}
                if isinstance(raw_requirements, dict)
                else {}
            ),
            check=str(d.get("check", "")),
            install=str(d.get("install", "")),
            version=str(d["version"]) if d.get("version") is not None else None,
            source=str(d.get("source", "")),
            description=str(d.get("description", "")),
            repo_url=str(d.get("repo_url", "")),
            docs=_docs_value(d.get("docs", [])),
            evidence=_string_dict_list_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_value(d.get("notes", [])),
            credentials=_string_list_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class EnvironmentMCPServerManifest:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    placement: str = "peer"
    distribution: str = "command"
    requirements: dict[str, str] = field(default_factory=dict)
    check: str = ""
    install: str = ""
    version: str | None = None
    source: str = ""
    description: str = ""
    repo_url: str = ""
    docs: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    install_prompt: str = ""
    verify_prompt: str = ""
    notes: list[str] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)
    risk_level: str = ""
    last_action: str = ""
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "cwd": self.cwd,
            "placement": self.placement,
            "distribution": self.distribution,
            "requirements": self.requirements,
            "check": self.check,
            "install": self.install,
            "source": self.source,
            "description": self.description,
            "repo_url": self.repo_url,
            "docs": [dict(item) for item in self.docs],
            "evidence": [dict(item) for item in self.evidence],
            "install_prompt": self.install_prompt,
            "verify_prompt": self.verify_prompt,
            "notes": list(self.notes),
            "credentials": list(self.credentials),
            "risk_level": self.risk_level,
            "last_action": self.last_action,
            "last_updated": self.last_updated,
        }
        if self.version is not None:
            data["version"] = self.version
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentMCPServerManifest":
        raw_args = d.get("args", [])
        raw_env = d.get("env", {})
        raw_requirements = d.get("requirements", {})
        return cls(
            name=str(d.get("name", "")),
            command=str(d.get("command", "")),
            args=[str(item) for item in raw_args] if isinstance(raw_args, list) else [],
            env=(
                {str(k): str(v) for k, v in raw_env.items()}
                if isinstance(raw_env, dict)
                else {}
            ),
            cwd=str(d["cwd"]) if d.get("cwd") is not None else None,
            placement=str(d.get("placement", "peer") or "peer"),
            distribution=str(d.get("distribution", "command") or "command"),
            requirements=(
                {str(k): str(v) for k, v in raw_requirements.items()}
                if isinstance(raw_requirements, dict)
                else {}
            ),
            check=str(d.get("check", "")),
            install=str(d.get("install", "")),
            version=str(d["version"]) if d.get("version") is not None else None,
            source=str(d.get("source", "")),
            description=str(d.get("description", "")),
            repo_url=str(d.get("repo_url", "")),
            docs=_docs_value(d.get("docs", [])),
            evidence=_string_dict_list_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_value(d.get("notes", [])),
            credentials=_string_list_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class EnvironmentSkillManifest:
    name: str
    enabled: bool = True
    scope: str = "project"
    check: str = ""
    install: str = ""
    version: str | None = None
    source: str = ""
    description: str = ""
    path_hint: str | None = None
    requirements: dict[str, str] = field(default_factory=dict)
    repo_url: str = ""
    docs: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    install_prompt: str = ""
    verify_prompt: str = ""
    notes: list[str] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)
    risk_level: str = ""
    last_action: str = ""
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "enabled": self.enabled,
            "scope": self.scope,
            "check": self.check,
            "install": self.install,
            "source": self.source,
            "description": self.description,
            "requirements": dict(self.requirements),
            "repo_url": self.repo_url,
            "docs": [dict(item) for item in self.docs],
            "evidence": [dict(item) for item in self.evidence],
            "install_prompt": self.install_prompt,
            "verify_prompt": self.verify_prompt,
            "notes": list(self.notes),
            "credentials": list(self.credentials),
            "risk_level": self.risk_level,
            "last_action": self.last_action,
            "last_updated": self.last_updated,
        }
        if self.version is not None:
            data["version"] = self.version
        if self.path_hint is not None:
            data["path_hint"] = self.path_hint
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentSkillManifest":
        raw_requirements = d.get("requirements", {})
        return cls(
            name=str(d.get("name", "")),
            enabled=_bool_value(d.get("enabled", True)),
            scope=str(d.get("scope", "project") or "project"),
            check=str(d.get("check", "")),
            install=str(d.get("install", "")),
            version=str(d["version"]) if d.get("version") is not None else None,
            source=str(d.get("source", "")),
            description=str(d.get("description", "")),
            path_hint=(
                str(d["path_hint"]) if d.get("path_hint") is not None else None
            ),
            requirements=(
                {str(k): str(v) for k, v in raw_requirements.items()}
                if isinstance(raw_requirements, dict)
                else {}
            ),
            repo_url=str(d.get("repo_url", "")),
            docs=_docs_value(d.get("docs", [])),
            evidence=_string_dict_list_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_value(d.get("notes", [])),
            credentials=_string_list_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class EnvironmentManifestRequest:
    peer_token: str
    os: str
    arch: str
    workspace: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "os": self.os,
            "arch": self.arch,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentManifestRequest":
        return cls(
            peer_token=d["peer_token"],
            os=str(d.get("os", "")),
            arch=str(d.get("arch", "")),
            workspace=str(d.get("workspace", "")),
        )


@dataclass
class EnvironmentManifestResponse:
    cli_tools: list[EnvironmentCLIToolManifest] = field(default_factory=list)
    mcp_servers: list[EnvironmentMCPServerManifest] = field(default_factory=list)
    skills: list[EnvironmentSkillManifest] = field(default_factory=list)
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cli_tools": [tool.to_dict() for tool in self.cli_tools],
            "mcp_servers": [server.to_dict() for server in self.mcp_servers],
            "skills": [skill.to_dict() for skill in self.skills],
            "prompt": self.prompt,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentManifestResponse":
        return cls(
            cli_tools=[
                EnvironmentCLIToolManifest.from_dict(item)
                for item in d.get("cli_tools", [])
                if isinstance(item, dict)
            ],
            mcp_servers=[
                EnvironmentMCPServerManifest.from_dict(item)
                for item in d.get("mcp_servers", [])
                if isinstance(item, dict)
            ],
            skills=[
                EnvironmentSkillManifest.from_dict(item)
                for item in d.get("skills", [])
                if isinstance(item, dict)
            ],
            prompt=str(d.get("prompt", "")),
        )


# ---------------------------------------------------------------------------
# Chat proxy (interactive peer -> host agent)
# ---------------------------------------------------------------------------


@dataclass
class ChatRequest:
    peer_token: str
    prompt: str
    mode: str | None = None
    workflow_mode: str | None = None
    taskflow_goal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"peer_token": self.peer_token, "prompt": self.prompt}
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.workflow_mode is not None:
            payload["workflow_mode"] = self.workflow_mode
        if self.taskflow_goal_id is not None:
            payload["taskflow_goal_id"] = self.taskflow_goal_id
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatRequest":
        return cls(
            peer_token=d["peer_token"],
            prompt=d["prompt"],
            mode=d.get("mode"),
            workflow_mode=d.get("workflow_mode"),
            taskflow_goal_id=d.get("taskflow_goal_id") or d.get("goal_id"),
        )


@dataclass
class ChatResponse:
    response: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"response": self.response, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatResponse":
        return cls(response=d.get("response", ""), error=d.get("error"))


@dataclass
class ChatStartRequest:
    peer_token: str
    prompt: str
    session_hint: str | None = None
    mode: str | None = None
    workflow_mode: str | None = None
    taskflow_goal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "peer_token": self.peer_token,
            "prompt": self.prompt,
            "session_hint": self.session_hint,
        }
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.workflow_mode is not None:
            payload["workflow_mode"] = self.workflow_mode
        if self.taskflow_goal_id is not None:
            payload["taskflow_goal_id"] = self.taskflow_goal_id
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatStartRequest":
        return cls(
            peer_token=d["peer_token"],
            prompt=d["prompt"],
            session_hint=d.get("session_hint"),
            mode=d.get("mode"),
            workflow_mode=d.get("workflow_mode"),
            taskflow_goal_id=d.get("taskflow_goal_id") or d.get("goal_id"),
        )


@dataclass
class ChatStartResponse:
    chat_id: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"chat_id": self.chat_id, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatStartResponse":
        return cls(chat_id=d.get("chat_id", ""), error=d.get("error"))


@dataclass
class ChatStreamRequest:
    peer_token: str
    chat_id: str
    cursor: int = 0
    timeout_sec: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "chat_id": self.chat_id,
            "cursor": self.cursor,
            "timeout_sec": self.timeout_sec,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatStreamRequest":
        return cls(
            peer_token=d["peer_token"],
            chat_id=d["chat_id"],
            cursor=int(d.get("cursor", 0)),
            timeout_sec=float(d.get("timeout_sec", 30.0)),
        )


@dataclass
class ChatStreamResponse:
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    next_cursor: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "done": self.done,
            "next_cursor": self.next_cursor,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatStreamResponse":
        return cls(
            events=list(d.get("events", [])),
            done=bool(d.get("done", False)),
            next_cursor=int(d.get("next_cursor", 0)),
            error=d.get("error"),
        )


@dataclass
class ChatCancelRequest:
    peer_token: str
    chat_id: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "chat_id": self.chat_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatCancelRequest":
        return cls(
            peer_token=d["peer_token"],
            chat_id=d["chat_id"],
            reason=d.get("reason"),
        )


@dataclass
class ChatCancelResponse:
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatCancelResponse":
        return cls(ok=bool(d.get("ok", False)), error=d.get("error"))


@dataclass
class SessionListRequest:
    peer_token: str
    limit: int = 20

    def to_dict(self) -> dict[str, Any]:
        return {"peer_token": self.peer_token, "limit": self.limit}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionListRequest":
        return cls(peer_token=d["peer_token"], limit=int(d.get("limit", 20)))


@dataclass
class SessionLoadRequest:
    peer_token: str
    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"peer_token": self.peer_token, "session_id": self.session_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionLoadRequest":
        return cls(peer_token=d["peer_token"], session_id=d["session_id"])


@dataclass
class SessionNewRequest:
    peer_token: str

    def to_dict(self) -> dict[str, Any]:
        return {"peer_token": self.peer_token}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionNewRequest":
        return cls(peer_token=d["peer_token"])


@dataclass
class SessionDeleteRequest:
    peer_token: str
    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"peer_token": self.peer_token, "session_id": self.session_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionDeleteRequest":
        return cls(peer_token=d["peer_token"], session_id=d["session_id"])


@dataclass
class SessionSnapshotRequest:
    peer_token: str
    session_id: str
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "session_id": self.session_id,
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionSnapshotRequest":
        snapshot = d.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        return cls(
            peer_token=d["peer_token"],
            session_id=d["session_id"],
            snapshot=snapshot,
        )


@dataclass
class SessionModelSwitchRequest:
    peer_token: str
    provider_id: str
    model_id: str
    session_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "peer_token": self.peer_token,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
        }
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        if self.parameters:
            payload["parameters"] = self.parameters
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionModelSwitchRequest":
        parameters = d.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        return cls(
            peer_token=d["peer_token"],
            provider_id=str(d.get("provider_id") or d.get("provider") or ""),
            model_id=str(d.get("model_id") or d.get("model") or ""),
            session_id=str(d["session_id"]) if d.get("session_id") is not None else None,
            parameters=dict(parameters),
        )


@dataclass
class ApprovalReplyRequest:
    peer_token: str
    chat_id: str
    approval_id: str
    decision: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_token": self.peer_token,
            "chat_id": self.chat_id,
            "approval_id": self.approval_id,
            "decision": self.decision,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalReplyRequest":
        return cls(
            peer_token=d["peer_token"],
            chat_id=d["chat_id"],
            approval_id=d["approval_id"],
            decision=d["decision"],
            reason=d.get("reason"),
        )


@dataclass
class ApprovalReplyResponse:
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalReplyResponse":
        return cls(ok=bool(d.get("ok", False)), error=d.get("error"))


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


@dataclass
class ExecToolRequest:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    cwd: str | None = None
    timeout_sec: int = 30
    expected_state: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args": self.args,
            "cwd": self.cwd,
            "timeout_sec": self.timeout_sec,
            "expected_state": self.expected_state,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecToolRequest":
        return cls(
            tool_name=d["tool_name"],
            args=d.get("args", {}),
            cwd=d.get("cwd"),
            timeout_sec=d.get("timeout_sec", 30),
            tool_call_id=d.get("tool_call_id"),
            expected_state=(
                dict(d.get("expected_state", {}))
                if isinstance(d.get("expected_state", {}), dict)
                else {}
            ),
        )


@dataclass
class ExecToolResult:
    ok: bool
    result: str = ""
    error_code: str | None = None
    error_message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": self.result,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecToolResult":
        return cls(
            ok=d["ok"],
            result=d.get("result", ""),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            meta=d.get("meta", {}),
        )


@dataclass
class ToolPreviewRequest:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    cwd: str | None = None
    timeout_sec: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args": self.args,
            "cwd": self.cwd,
            "timeout_sec": self.timeout_sec,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolPreviewRequest":
        return cls(
            tool_name=d["tool_name"],
            args=d.get("args", {}),
            cwd=d.get("cwd"),
            timeout_sec=d.get("timeout_sec", 30),
        )


@dataclass
class ToolPreviewResult:
    ok: bool
    sections: list[dict[str, Any]] = field(default_factory=list)
    resolved_path: str | None = None
    old_sha256: str | None = None
    old_exists: bool | None = None
    old_size: int | None = None
    old_mtime_ns: int | None = None
    diff: str = ""
    original_text: str | None = None
    modified_text: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sections": self.sections,
            "resolved_path": self.resolved_path,
            "old_sha256": self.old_sha256,
            "old_exists": self.old_exists,
            "old_size": self.old_size,
            "old_mtime_ns": self.old_mtime_ns,
            "diff": self.diff,
            "original_text": self.original_text,
            "modified_text": self.modified_text,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolPreviewResult":
        return cls(
            ok=bool(d.get("ok", False)),
            sections=[
                dict(item)
                for item in d.get("sections", [])
                if isinstance(item, dict)
            ],
            resolved_path=d.get("resolved_path"),
            old_sha256=d.get("old_sha256"),
            old_exists=(
                bool(d["old_exists"]) if d.get("old_exists") is not None else None
            ),
            old_size=int(d["old_size"]) if d.get("old_size") is not None else None,
            old_mtime_ns=(
                int(d["old_mtime_ns"]) if d.get("old_mtime_ns") is not None else None
            ),
            diff=str(d.get("diff", "")),
            original_text=d.get("original_text"),
            modified_text=d.get("modified_text"),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            meta=d.get("meta", {}) if isinstance(d.get("meta", {}), dict) else {},
        )


# ---------------------------------------------------------------------------
# Stream chunk (MVP: shell only if needed; struct kept for forward-compat)
# ---------------------------------------------------------------------------


@dataclass
class ToolStreamChunk:
    chunk_type: str  # "stdout" | "stderr" | "exit"
    data: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_type": self.chunk_type, "data": self.data, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolStreamChunk":
        return cls(
            chunk_type=d["chunk_type"],
            data=d.get("data", ""),
            meta=d.get("meta", {}),
        )


# ---------------------------------------------------------------------------
# Disconnect / Cleanup
# ---------------------------------------------------------------------------


@dataclass
class DisconnectNotice:
    reason: str = "peer_initiated"

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DisconnectNotice":
        return cls(reason=d.get("reason", "peer_initiated"))


@dataclass
class CleanupRequest:
    pass

    def to_dict(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CleanupRequest":
        return cls()


@dataclass
class CleanupResult:
    ok: bool
    removed_items: list[str] = field(default_factory=list)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "removed_items": self.removed_items,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CleanupResult":
        return cls(
            ok=d["ok"],
            removed_items=d.get("removed_items", []),
            error_message=d.get("error_message"),
        )


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------


@dataclass
class ErrorMessage:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ErrorMessage":
        return cls(code=d["code"], message=d.get("message", ""))
