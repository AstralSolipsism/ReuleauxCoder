"""Configuration models - domain layer configuration abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


MCPPlacement = Literal["server", "peer", "both"]
MCPDistribution = Literal["command", "artifact"]
CLIPlacement = Literal["server", "local", "both"]
ProviderType = Literal["openai_chat", "anthropic_messages", "openai_responses"]
ProviderCompat = Literal["generic", "deepseek", "kimi", "glm", "qwen", "zenmux"]

SUPPORTED_PROVIDER_COMPATS = {"generic", "deepseek", "kimi", "glm", "qwen", "zenmux"}


def normalize_provider_compat(value: Any) -> ProviderCompat:
    """Normalize a configured provider compatibility profile."""

    normalized = str(value or "generic").strip().lower()
    if normalized in SUPPORTED_PROVIDER_COMPATS:
        return normalized  # type: ignore[return-value]
    return "generic"


def infer_provider_compat(base_url: str | None) -> ProviderCompat:
    """Infer a provider compat profile from a known service endpoint."""

    url = str(base_url or "").lower()
    if "api.deepseek.com" in url:
        return "deepseek"
    if "moonshot" in url or "kimi" in url:
        return "kimi"
    if "bigmodel.cn" in url or "zhipu" in url or "z.ai" in url:
        return "glm"
    if "dashscope" in url or "aliyuncs.com" in url or "bailian" in url:
        return "qwen"
    if "zenmux.ai" in url:
        return "zenmux"
    return "generic"


@dataclass
class ProviderCapabilities:
    """Declared LLM provider capabilities used for request shaping."""

    chat: bool = True
    streaming: bool = True
    tools: bool = True
    parallel_tools: bool = True
    tool_choice_required: bool = False
    reasoning_effort: bool = False
    thinking: bool = False
    thinking_signature: bool = False
    image_input: bool = False
    responses_api: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "chat": self.chat,
            "streaming": self.streaming,
            "tools": self.tools,
            "parallel_tools": self.parallel_tools,
            "tool_choice_required": self.tool_choice_required,
            "reasoning_effort": self.reasoning_effort,
            "thinking": self.thinking,
            "thinking_signature": self.thinking_signature,
            "image_input": self.image_input,
            "responses_api": self.responses_api,
        }

    @classmethod
    def defaults_for(cls, provider_type: str) -> "ProviderCapabilities":
        normalized = provider_type.strip().lower()
        if normalized == "anthropic_messages":
            return cls(
                tools=True,
                parallel_tools=True,
                tool_choice_required=True,
                thinking=True,
                thinking_signature=True,
            )
        if normalized == "openai_responses":
            return cls(
                tools=True,
                parallel_tools=True,
                tool_choice_required=True,
                reasoning_effort=True,
                responses_api=True,
            )
        return cls(
            tools=True,
            parallel_tools=True,
            tool_choice_required=False,
            reasoning_effort=True,
            thinking=True,
        )

    @classmethod
    def from_dict(
        cls, d: dict[str, Any] | None, *, provider_type: str = "openai_chat"
    ) -> "ProviderCapabilities":
        defaults = cls.defaults_for(provider_type)
        if not isinstance(d, dict):
            return defaults
        data = defaults.to_dict()
        for key, value in d.items():
            if key in data:
                data[key] = bool(value)
        return cls(**data)


@dataclass
class ProviderConfig:
    """Server-side LLM provider configuration."""

    id: str
    type: ProviderType = "openai_chat"
    compat: ProviderCompat = "generic"
    enabled: bool = True
    api_key: str = ""
    base_url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 120
    max_retries: int = 3
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities.defaults_for("openai_chat")
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type,
            "compat": self.compat,
            "enabled": self.enabled,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "headers": dict(self.headers),
            "timeout_sec": self.timeout_sec,
            "max_retries": self.max_retries,
            "capabilities": self.capabilities.to_dict(),
            "extra": dict(self.extra),
        }
        return data

    @classmethod
    def from_dict(cls, provider_id: str, d: dict[str, Any]) -> "ProviderConfig":
        raw_type = str(d.get("type", "openai_chat")).strip().lower()
        provider_type: ProviderType
        if raw_type in {"anthropic_messages", "openai_responses"}:
            provider_type = raw_type  # type: ignore[assignment]
        else:
            provider_type = "openai_chat"
        raw_headers = d.get("headers", {})
        raw_extra = d.get("extra", {})
        base_url = str(d["base_url"]) if d.get("base_url") is not None else None
        compat = (
            normalize_provider_compat(d.get("compat"))
            if d.get("compat") is not None
            else infer_provider_compat(base_url)
        )
        return cls(
            id=provider_id,
            type=provider_type,
            compat=compat,
            enabled=bool(d.get("enabled", True)),
            api_key=str(d.get("api_key", "") or ""),
            base_url=base_url,
            headers=(
                {str(k): str(v) for k, v in raw_headers.items()}
                if isinstance(raw_headers, dict)
                else {}
            ),
            timeout_sec=int(d.get("timeout_sec", 120) or 120),
            max_retries=int(d.get("max_retries", 3) or 3),
            capabilities=ProviderCapabilities.from_dict(
                d.get("capabilities"), provider_type=provider_type
            ),
            extra=dict(raw_extra) if isinstance(raw_extra, dict) else {},
        )


@dataclass
class ProvidersConfig:
    """Configured LLM providers keyed by provider id."""

    items: dict[str, ProviderConfig] = field(default_factory=dict)


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
            enabled=_bool_config_value(d.get("enabled", True)),
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
            repo_url=str(d.get("repo_url", "")),
            docs=_docs_config_value(d.get("docs", [])),
            evidence=_string_dict_list_config_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_config_value(d.get("notes", [])),
            credentials=_string_list_config_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class ModelProfileConfig:
    """Named model/runtime profile used by ``/model`` switching."""

    name: str
    model: str
    api_key: str
    provider: Optional[str] = None
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
            "provider": self.provider,
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
            provider=str(d["provider"]) if d.get("provider") is not None else None,
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
    token_fudge_factor: float = 1.1


@dataclass
class RemoteExecConfig:
    """Remote execution relay configuration."""

    enabled: bool = False
    host_mode: bool = False
    relay_bind: str = "127.0.0.1:8765"
    bootstrap_access_secret: str = ""
    admin_access_secret: str = ""
    bootstrap_token_ttl_sec: int = 300
    peer_token_ttl_sec: int = 3600
    heartbeat_interval_sec: int = 10
    heartbeat_timeout_sec: int = 30
    default_tool_timeout_sec: int = 30
    shell_timeout_sec: int = 120


@dataclass
class AgentRuntimeConfig:
    """Global server-side Agent runtime limits."""

    max_running_agents: int = 4
    max_shells_per_agent: int = 1

    def to_dict(self) -> dict[str, int]:
        return {
            "max_running_agents": self.max_running_agents,
            "max_shells_per_agent": self.max_shells_per_agent,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "AgentRuntimeConfig":
        if not isinstance(d, dict):
            return cls()
        return cls(
            max_running_agents=int(d.get("max_running_agents", 4) or 4),
            max_shells_per_agent=int(d.get("max_shells_per_agent", 1) or 1),
        )


@dataclass
class EnvironmentCLIToolConfig:
    """Declarative CLI tool entry used by lightweight environment sync."""

    name: str
    command: str = ""
    enabled: bool = True
    placement: CLIPlacement = "local"
    capabilities: list[str] = field(default_factory=list)
    requirements: dict[str, str] = field(default_factory=dict)
    check: str = ""
    install: str = ""
    version: Optional[str] = None
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
            "command": self.command,
            "enabled": self.enabled,
            "placement": self.placement,
            "capabilities": list(self.capabilities),
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
    def from_dict(cls, name: str, d: dict[str, Any]) -> "EnvironmentCLIToolConfig":
        raw_capabilities = d.get("capabilities", [])
        raw_requirements = d.get("requirements", {})
        raw_placement = str(d.get("placement", "local")).lower()
        placement: CLIPlacement
        if raw_placement in {"server", "both"}:
            placement = raw_placement  # type: ignore[assignment]
        else:
            placement = "local"
        return cls(
            name=name,
            command=str(d.get("command", "")),
            enabled=_bool_config_value(d.get("enabled", True)),
            placement=placement,
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
            docs=_docs_config_value(d.get("docs", [])),
            evidence=_string_dict_list_config_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_config_value(d.get("notes", [])),
            credentials=_string_list_config_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class EnvironmentSkillConfig:
    """Declarative skill entry used by lightweight environment sync."""

    name: str
    enabled: bool = True
    scope: str = "project"
    check: str = ""
    install: str = ""
    version: Optional[str] = None
    source: str = ""
    description: str = ""
    path_hint: Optional[str] = None
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
    def from_dict(cls, name: str, d: dict[str, Any]) -> "EnvironmentSkillConfig":
        raw_requirements = d.get("requirements", {})
        return cls(
            name=name,
            enabled=_bool_config_value(d.get("enabled", True)),
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
            docs=_docs_config_value(d.get("docs", [])),
            evidence=_string_dict_list_config_value(d.get("evidence", [])),
            install_prompt=str(d.get("install_prompt", "")),
            verify_prompt=str(d.get("verify_prompt", "")),
            notes=_string_list_config_value(d.get("notes", [])),
            credentials=_string_list_config_value(d.get("credentials", [])),
            risk_level=str(d.get("risk_level", "")),
            last_action=str(d.get("last_action", "")),
            last_updated=str(d.get("last_updated", "")),
        )


@dataclass
class EnvironmentConfig:
    """Server-authoritative lightweight CLI environment manifest."""

    cli_tools: dict[str, EnvironmentCLIToolConfig] = field(default_factory=dict)
    skills: dict[str, EnvironmentSkillConfig] = field(default_factory=dict)


def _bool_config_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _string_list_config_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _docs_config_value(value: Any) -> list[dict[str, str]]:
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


def _string_dict_list_config_value(value: Any) -> list[dict[str, str]]:
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
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
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

    # Server Agent runtime settings
    agent_runtime: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)

    # Server-authoritative environment manifest
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        has_provider_backed_key = any(
            provider.api_key for provider in self.providers.items.values()
        )
        if not self.api_key and not has_provider_backed_key:
            errors.append("api_key is required")
        if self.max_tokens < 1:
            errors.append("max_tokens must be positive")
        if self.temperature < 0 or self.temperature > 2:
            errors.append("temperature must be between 0 and 2")
        if self.tool_output_max_chars < 1:
            errors.append("tool_output_max_chars must be positive")
        if self.tool_output_max_lines < 1:
            errors.append("tool_output_max_lines must be positive")
        if self.agent_runtime.max_running_agents < 1:
            errors.append("agent_runtime.max_running_agents must be positive")
        if self.agent_runtime.max_shells_per_agent < 1:
            errors.append("agent_runtime.max_shells_per_agent must be positive")
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
            if profile.provider:
                if profile.provider not in self.providers.items:
                    errors.append(
                        f"model_profiles[{name}].provider must exist in providers.items"
                    )
            elif not profile.api_key:
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

        for provider_id, provider in self.providers.items.items():
            if provider.compat not in SUPPORTED_PROVIDER_COMPATS:
                errors.append(
                    f"providers.items[{provider_id}].compat must be one of deepseek, generic, glm, kimi, qwen, zenmux"
                )
            if provider.type not in {
                "openai_chat",
                "anthropic_messages",
                "openai_responses",
            }:
                errors.append(
                    f"providers.items[{provider_id}].type must be one of openai_chat, anthropic_messages, openai_responses"
                )
            if provider.timeout_sec < 1:
                errors.append(
                    f"providers.items[{provider_id}].timeout_sec must be positive"
                )
            if provider.max_retries < 0:
                errors.append(
                    f"providers.items[{provider_id}].max_retries must be non-negative"
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
