"""Configuration models - domain layer configuration abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


MCPPlacement = Literal["server", "peer", "both"]
MCPDistribution = Literal["command", "artifact"]


@dataclass
class MCPLaunchConfig:
    """Launch command for a peer-hosted MCP server."""

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPLaunchConfig":
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
class MCPArtifactConfig:
    """Versioned artifact for a peer-hosted MCP server."""

    path: str
    sha256: str
    launch: MCPLaunchConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "sha256": self.sha256}
        if self.launch is not None:
            data["launch"] = self.launch.to_dict()
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPArtifactConfig":
        raw_launch = d.get("launch")
        return cls(
            path=str(d.get("path", "")),
            sha256=str(d.get("sha256", "")),
            launch=(
                MCPLaunchConfig.from_dict(raw_launch)
                if isinstance(raw_launch, dict)
                else None
            ),
        )


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    enabled: bool = True
    placement: MCPPlacement = "server"
    distribution: MCPDistribution = "command"
    version: Optional[str] = None
    launch: MCPLaunchConfig | None = None
    artifacts: dict[str, MCPArtifactConfig] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    requirements: dict[str, str] = field(default_factory=dict)
    build: dict[str, Any] = field(default_factory=dict)
    check: str = ""
    install: str = ""
    source: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary format for serialization."""
        return {
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "cwd": self.cwd,
            "enabled": self.enabled,
            "placement": self.placement,
            "distribution": self.distribution,
            "version": self.version,
            "launch": self.launch.to_dict() if self.launch else None,
            "artifacts": {
                platform: artifact.to_dict()
                for platform, artifact in self.artifacts.items()
            },
            "permissions": self.permissions,
            "requirements": self.requirements,
            "build": self.build,
            "check": self.check,
            "install": self.install,
            "source": self.source,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "MCPServerConfig":
        """Create from dictionary format."""
        raw_placement = str(d.get("placement", "server")).lower()
        placement: MCPPlacement
        if raw_placement in {"peer", "both"}:
            placement = raw_placement  # type: ignore[assignment]
        else:
            placement = "server"
        raw_distribution = str(d.get("distribution", "")).lower()
        raw_artifacts = d.get("artifacts", {})
        artifacts = (
            {
                str(platform): MCPArtifactConfig.from_dict(artifact)
                for platform, artifact in raw_artifacts.items()
                if isinstance(artifact, dict)
            }
            if isinstance(raw_artifacts, dict)
            else {}
        )
        distribution: MCPDistribution
        if raw_distribution in {"command", "artifact"}:
            distribution = raw_distribution  # type: ignore[assignment]
        elif artifacts:
            distribution = "artifact"
        else:
            distribution = "command"
        raw_launch = d.get("launch")
        launch = (
            MCPLaunchConfig.from_dict(raw_launch)
            if isinstance(raw_launch, dict)
            else None
        )
        raw_permissions = d.get("permissions", {})
        raw_requirements = d.get("requirements", {})
        raw_build = d.get("build", {})
        raw_args = d.get("args", [])
        raw_env = d.get("env", {})
        return cls(
            name=name,
            command=str(d.get("command", "")),
            args=[str(arg) for arg in raw_args] if isinstance(raw_args, list) else [],
            env=(
                {str(k): str(v) for k, v in raw_env.items()}
                if isinstance(raw_env, dict)
                else {}
            ),
            cwd=d.get("cwd"),
            enabled=d.get("enabled", True),
            placement=placement,
            distribution=distribution,
            version=str(d["version"]) if d.get("version") is not None else None,
            launch=launch,
            artifacts=artifacts,
            permissions=(
                dict(raw_permissions) if isinstance(raw_permissions, dict) else {}
            ),
            requirements=(
                {str(k): str(v) for k, v in raw_requirements.items()}
                if isinstance(raw_requirements, dict)
                else {}
            ),
            build=dict(raw_build) if isinstance(raw_build, dict) else {},
            check=str(d.get("check", "")),
            install=str(d.get("install", "")),
            source=str(d.get("source", "")),
            description=str(d.get("description", "")),
        )


@dataclass
class ModelProfileConfig:
    """Named model/runtime profile used by ``/model`` switching."""

    name: str
    model: str
    api_key: str
    base_url: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    preserve_reasoning_content: bool = True
    backfill_reasoning_content_for_tool_calls: bool = False
    reasoning_effort: Optional[str] = None
    thinking_enabled: Optional[bool] = None
    reasoning_replay_mode: Optional[str] = None
    reasoning_replay_placeholder: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary format for serialization."""
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "max_context_tokens": self.max_context_tokens,
            "preserve_reasoning_content": self.preserve_reasoning_content,
            "backfill_reasoning_content_for_tool_calls": self.backfill_reasoning_content_for_tool_calls,
            "reasoning_effort": self.reasoning_effort,
            "thinking_enabled": self.thinking_enabled,
            "reasoning_replay_mode": self.reasoning_replay_mode,
            "reasoning_replay_placeholder": self.reasoning_replay_placeholder,
        }

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "ModelProfileConfig":
        """Create from dictionary format."""
        return cls(
            name=name,
            model=d.get("model", "gpt-4o"),
            api_key=d.get("api_key", ""),
            base_url=d.get("base_url"),
            max_tokens=d.get("max_tokens", 4096),
            temperature=d.get("temperature", 0.0),
            max_context_tokens=d.get("max_context_tokens", 128_000),
            preserve_reasoning_content=d.get("preserve_reasoning_content", True),
            backfill_reasoning_content_for_tool_calls=d.get(
                "backfill_reasoning_content_for_tool_calls", False
            ),
            reasoning_effort=d.get("reasoning_effort"),
            thinking_enabled=d.get("thinking_enabled"),
            reasoning_replay_mode=d.get("reasoning_replay_mode"),
            reasoning_replay_placeholder=d.get("reasoning_replay_placeholder"),
        )


@dataclass
class ModeConfig:
    """Configuration for one agent mode."""

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    prompt_append: str = ""
    allowed_subagent_modes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "ModeConfig":
        """Create from dictionary format."""
        tools = d.get("tools", [])
        allowed_subagent_modes = d.get("allowed_subagent_modes", [])
        return cls(
            name=name,
            description=d.get("description", "") or "",
            tools=[str(t) for t in tools] if isinstance(tools, list) else [],
            prompt_append=d.get("prompt_append", "") or "",
            allowed_subagent_modes=(
                [str(m) for m in allowed_subagent_modes]
                if isinstance(allowed_subagent_modes, list)
                else []
            ),
        )


ApprovalAction = Literal["allow", "warn", "require_approval", "deny"]


@dataclass
class ApprovalRuleConfig:
    """User-configurable approval rule."""

    tool_name: Optional[str] = None
    tool_source: Optional[str] = None
    mcp_server: Optional[str] = None
    effect_class: Optional[str] = None
    profile: Optional[str] = None
    action: ApprovalAction = "require_approval"


@dataclass
class ApprovalConfig:
    """Approval policy configuration."""

    default_mode: ApprovalAction = "require_approval"
    rules: list[ApprovalRuleConfig] = field(default_factory=list)


@dataclass
class SkillsConfig:
    """Skills discovery/runtime configuration."""

    enabled: bool = True
    scan_project: bool = True
    scan_user: bool = True
    disabled: list[str] = field(default_factory=list)


@dataclass
class PromptConfig:
    """User/workspace prompt customization."""

    system_append: str = ""


@dataclass
class ContextConfig:
    """Context compression configuration."""

    snip_keep_recent_tools: int = 5
    snip_threshold_chars: int = 1500
    snip_min_lines: int = 6
    summarize_keep_recent_turns: int = 5


@dataclass
class RemoteExecConfig:
    """Remote execution relay configuration."""

    enabled: bool = False
    host_mode: bool = False
    relay_bind: str = "127.0.0.1:8765"
    bootstrap_access_secret: str = ""
    bootstrap_token_ttl_sec: int = 300
    peer_token_ttl_sec: int = 3600
    heartbeat_interval_sec: int = 10
    heartbeat_timeout_sec: int = 30
    default_tool_timeout_sec: int = 30
    shell_timeout_sec: int = 120


@dataclass
class EnvironmentCLIToolConfig:
    """Declarative CLI tool entry used by lightweight environment sync."""

    name: str
    command: str = ""
    capabilities: list[str] = field(default_factory=list)
    check: str = ""
    install: str = ""
    version: Optional[str] = None
    source: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "command": self.command,
            "capabilities": list(self.capabilities),
            "check": self.check,
            "install": self.install,
            "source": self.source,
            "description": self.description,
        }
        if self.version is not None:
            data["version"] = self.version
        return data

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "EnvironmentCLIToolConfig":
        raw_capabilities = d.get("capabilities", [])
        return cls(
            name=name,
            command=str(d.get("command", "")),
            capabilities=(
                [str(item) for item in raw_capabilities]
                if isinstance(raw_capabilities, list)
                else []
            ),
            check=str(d.get("check", "")),
            install=str(d.get("install", "")),
            version=str(d["version"]) if d.get("version") is not None else None,
            source=str(d.get("source", "")),
            description=str(d.get("description", "")),
        )


@dataclass
class EnvironmentConfig:
    """Server-authoritative lightweight CLI environment manifest."""

    cli_tools: dict[str, EnvironmentCLIToolConfig] = field(default_factory=dict)


@dataclass
class Config:
    """Main configuration model for ReuleauxCoder."""

    model: str = "gpt-4o"
    api_key: str = ""
    base_url: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    preserve_reasoning_content: bool = True
    backfill_reasoning_content_for_tool_calls: bool = False
    reasoning_effort: Optional[str] = None
    thinking_enabled: Optional[bool] = None
    reasoning_replay_mode: Optional[str] = None
    reasoning_replay_placeholder: Optional[str] = None
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    mcp_artifact_root: str = ".rcoder/mcp-artifacts"
    model_profiles: dict[str, ModelProfileConfig] = field(default_factory=dict)
    active_model_profile: Optional[str] = None
    active_main_model_profile: Optional[str] = None
    active_sub_model_profile: Optional[str] = None

    # Mode settings
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    active_mode: Optional[str] = None

    # Tool output settings
    tool_output_max_chars: int = 12_000
    tool_output_max_lines: int = 120
    tool_output_store_full: bool = True
    tool_output_store_dir: Optional[str] = None

    # Session settings
    session_auto_save: bool = True
    session_dir: Optional[str] = None

    # CLI settings
    history_file: Optional[str] = None
    llm_debug_trace: bool = False

    # Approval settings
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)

    # Skills settings
    skills: SkillsConfig = field(default_factory=SkillsConfig)

    # Prompt settings
    prompt: PromptConfig = field(default_factory=PromptConfig)

    # Context compression settings
    context: ContextConfig = field(default_factory=ContextConfig)

    # Remote execution settings
    remote_exec: RemoteExecConfig = field(default_factory=RemoteExecConfig)

    # Server-authoritative environment manifest
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        if not self.api_key:
            errors.append("api_key is required")
        if self.max_tokens < 1:
            errors.append("max_tokens must be positive")
        if self.temperature < 0 or self.temperature > 2:
            errors.append("temperature must be between 0 and 2")
        if self.tool_output_max_chars < 1:
            errors.append("tool_output_max_chars must be positive")
        if self.tool_output_max_lines < 1:
            errors.append("tool_output_max_lines must be positive")
        valid_actions = {"allow", "warn", "require_approval", "deny"}
        if (
            self.active_model_profile
            and self.active_model_profile not in self.model_profiles
        ):
            errors.append("active_model_profile must exist in model_profiles")
        if (
            self.active_main_model_profile
            and self.active_main_model_profile not in self.model_profiles
        ):
            errors.append("active_main_model_profile must exist in model_profiles")
        if (
            self.active_sub_model_profile
            and self.active_sub_model_profile not in self.model_profiles
        ):
            errors.append("active_sub_model_profile must exist in model_profiles")
        for name, profile in self.model_profiles.items():
            if not profile.api_key:
                errors.append(f"model_profiles[{name}].api_key is required")
            if profile.max_tokens < 1:
                errors.append(f"model_profiles[{name}].max_tokens must be positive")
            if profile.max_context_tokens < 1:
                errors.append(
                    f"model_profiles[{name}].max_context_tokens must be positive"
                )
            if profile.temperature < 0 or profile.temperature > 2:
                errors.append(
                    f"model_profiles[{name}].temperature must be between 0 and 2"
                )

        if self.active_mode and self.active_mode not in self.modes:
            errors.append("active_mode must exist in modes")
        for mode_name, mode in self.modes.items():
            if not mode.name:
                errors.append(f"modes[{mode_name}] must have a name")

        if self.approval.default_mode not in valid_actions:
            errors.append(
                "approval.default_mode must be one of allow, warn, require_approval, deny"
            )
        for i, rule in enumerate(self.approval.rules):
            if rule.action not in valid_actions:
                errors.append(
                    f"approval.rules[{i}].action must be one of allow, warn, require_approval, deny"
                )
        return errors

    def is_valid(self) -> bool:
        """Check if configuration is valid."""
        return len(self.validate()) == 0
