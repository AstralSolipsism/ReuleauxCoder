"""Domain models for configurable Agent runtime execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutorType(str, Enum):
    """Supported Agent executor families."""

    REULEAUXCODER = "reuleauxcoder"
    FAKE = "fake"
    CODEX = "codex"
    CLAUDE = "claude"
    GEMINI = "gemini"


class ExecutionLocation(str, Enum):
    """Where an Agent task runs."""

    REMOTE_SERVER = "remote_server"
    LOCAL_WORKSPACE = "local_workspace"
    DAEMON_WORKTREE = "daemon_worktree"


class TriggerMode(str, Enum):
    """How an Agent execution was triggered."""

    INTERACTIVE_CHAT = "interactive_chat"
    ISSUE_TASK = "issue_task"
    ENVIRONMENT_CONFIG = "environment_config"


class TaskStatus(str, Enum):
    """Task execution lifecycle status."""

    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class ArtifactType(str, Enum):
    """Deliverable type produced by a task."""

    BRANCH = "branch"
    PULL_REQUEST = "pull_request"
    TRANSCRIPT = "transcript"
    LOG = "log"
    DIFF = "diff"
    TEST_RESULT = "test_result"
    FINAL_REPORT = "final_report"
    REPORT = "report"
    COMMENT = "comment"
    DOCUMENT = "document"
    PLAN = "plan"


class ArtifactStatus(str, Enum):
    """Lifecycle status for a task artifact."""

    NONE = "none"
    GENERATED = "generated"
    BRANCH_CREATED = "branch_created"
    PUSHED = "pushed"
    PR_CREATED = "pr_created"
    PR_REVIEWING = "pr_reviewing"
    PR_CHANGES_REQUESTED = "pr_changes_requested"
    PR_APPROVED = "pr_approved"
    MERGED = "merged"
    CLOSED = "closed"
    FAILED = "failed"


class MergeStatus(str, Enum):
    """User-facing merge gate status for pull request artifacts."""

    PENDING_USER = "pending_user"
    MERGED_BY_USER = "merged_by_user"
    CLOSED = "closed"


def _enum_value(value: Enum | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(val)
        for key, val in value.items()
        if str(key).strip() and val is not None
    }


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _reject_plaintext_secret_container(data: dict[str, Any], *, owner: str) -> None:
    secret_keys = {"secret", "secrets", "api_key", "api_keys", "token", "tokens"}
    for key in data:
        if str(key).strip().lower() in secret_keys:
            raise ValueError(
                f"{owner} must reference secrets through credential_refs, not plaintext secrets"
            )


@dataclass
class AgentPromptConfig:
    """Prompt references and append-only instructions for an Agent."""

    agent_md: str | None = None
    system_append: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentPromptConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            agent_md=str(data["agent_md"]) if data.get("agent_md") is not None else None,
            system_append=str(data.get("system_append", "") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if self.agent_md:
            result["agent_md"] = self.agent_md
        if self.system_append:
            result["system_append"] = self.system_append
        return result


@dataclass
class AgentModelConfig:
    """Default model binding for an Agent profile."""

    provider: str = ""
    model: str = ""
    display_name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentModelConfig":
        if not isinstance(data, dict):
            return cls()
        parameters = data.get("parameters", {})
        return cls(
            provider=str(
                data.get("provider")
                or data.get("provider_id")
                or data.get("providerId")
                or ""
            ),
            model=str(
                data.get("model")
                or data.get("model_id")
                or data.get("modelId")
                or ""
            ),
            display_name=str(
                data.get("display_name") or data.get("displayName") or ""
            ),
            parameters=dict(parameters) if isinstance(parameters, dict) else {},
        )

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.provider:
            result["provider"] = self.provider
        if self.model:
            result["model"] = self.model
        if self.display_name:
            result["display_name"] = self.display_name
        if self.parameters:
            result["parameters"] = dict(self.parameters)
        return result


@dataclass
class RuntimeProfileConfig:
    """Runtime profile describing how to launch an Agent executor."""

    id: str
    executor: ExecutorType = ExecutorType.REULEAUXCODER
    execution_location: ExecutionLocation = ExecutionLocation.REMOTE_SERVER
    model: str = ""
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    runtime_home_policy: str = ""
    approval_mode: str = ""
    config_isolation: str = ""
    credential_refs: dict[str, str] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, profile_id: str, data: dict[str, Any] | None
    ) -> "RuntimeProfileConfig":
        if not isinstance(data, dict):
            data = {}
        _reject_plaintext_secret_container(data, owner="runtime profile")
        return cls(
            id=str(profile_id),
            executor=ExecutorType(str(data.get("executor", "reuleauxcoder"))),
            execution_location=ExecutionLocation(
                str(data.get("execution_location", "remote_server"))
            ),
            model=str(data.get("model", "") or ""),
            command=str(data["command"]) if data.get("command") is not None else None,
            args=_string_list(data.get("args", [])),
            env=_string_dict(data.get("env", {})),
            runtime_home_policy=str(data.get("runtime_home_policy", "") or ""),
            approval_mode=str(data.get("approval_mode", "") or ""),
            config_isolation=str(data.get("config_isolation", "") or ""),
            credential_refs=_string_dict(data.get("credential_refs", {})),
            mcp=_dict_value(data.get("mcp", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "executor": self.executor.value,
            "execution_location": self.execution_location.value,
        }
        if self.command is not None:
            result["command"] = self.command
        if self.model:
            result["model"] = self.model
        if self.args:
            result["args"] = list(self.args)
        if self.env:
            result["env"] = dict(self.env)
        if self.runtime_home_policy:
            result["runtime_home_policy"] = self.runtime_home_policy
        if self.approval_mode:
            result["approval_mode"] = self.approval_mode
        if self.config_isolation:
            result["config_isolation"] = self.config_isolation
        if self.credential_refs:
            result["credential_refs"] = dict(self.credential_refs)
        if self.mcp:
            result["mcp"] = dict(self.mcp)
        return result


@dataclass
class AgentConfig:
    """Server-authoritative Agent configuration."""

    id: str
    name: str = ""
    description: str = ""
    runtime_profile: str = ""
    capabilities: list[str] = field(default_factory=list)
    model: AgentModelConfig = field(default_factory=AgentModelConfig)
    prompt: AgentPromptConfig = field(default_factory=AgentPromptConfig)
    mcp: dict[str, Any] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    max_concurrent_tasks: int | None = None
    credential_refs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, agent_id: str, data: dict[str, Any] | None) -> "AgentConfig":
        if not isinstance(data, dict):
            data = {}
        _reject_plaintext_secret_container(data, owner="agent config")
        raw_max = data.get("max_concurrent_tasks")
        max_concurrent_tasks = int(raw_max) if raw_max is not None else None
        return cls(
            id=str(agent_id),
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            runtime_profile=str(data.get("runtime_profile", "") or ""),
            capabilities=_string_list(data.get("capabilities", [])),
            model=AgentModelConfig.from_dict(data.get("model")),
            prompt=AgentPromptConfig.from_dict(data.get("prompt")),
            mcp=_dict_value(data.get("mcp", {})),
            skills=_string_list(data.get("skills", [])),
            max_concurrent_tasks=max_concurrent_tasks,
            credential_refs=_string_dict(data.get("credential_refs", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.name:
            result["name"] = self.name
        if self.description:
            result["description"] = self.description
        if self.runtime_profile:
            result["runtime_profile"] = self.runtime_profile
        if self.capabilities:
            result["capabilities"] = list(self.capabilities)
        model = self.model.to_dict()
        if model:
            result["model"] = model
        prompt = self.prompt.to_dict()
        if prompt:
            result["prompt"] = prompt
        if self.mcp:
            result["mcp"] = dict(self.mcp)
        if self.skills:
            result["skills"] = list(self.skills)
        if self.max_concurrent_tasks is not None:
            result["max_concurrent_tasks"] = self.max_concurrent_tasks
        if self.credential_refs:
            result["credential_refs"] = dict(self.credential_refs)
        return result


@dataclass
class TaskRecord:
    """One execution attempt by an Agent."""

    id: str
    issue_id: str
    agent_id: str
    trigger_mode: TriggerMode = TriggerMode.ISSUE_TASK
    status: TaskStatus = TaskStatus.QUEUED
    prompt: str = ""
    runtime_profile_id: str | None = None
    executor: ExecutorType | None = None
    execution_location: ExecutionLocation | None = None
    output: str | None = None
    parent_task_id: str | None = None
    trigger_comment_id: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    worker_id: str | None = None
    executor_session_id: str | None = None
    workdir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.trigger_mode = TriggerMode(_enum_value(self.trigger_mode))
        self.status = TaskStatus(_enum_value(self.status))
        if self.executor is not None:
            self.executor = ExecutorType(_enum_value(self.executor))
        if self.execution_location is not None:
            self.execution_location = ExecutionLocation(
                _enum_value(self.execution_location)
            )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
        }


@dataclass
class TaskArtifact:
    """Artifact produced by a task."""

    id: str
    task_id: str
    type: ArtifactType
    status: ArtifactStatus = ArtifactStatus.NONE
    branch_name: str | None = None
    pr_url: str | None = None
    content: str | None = None
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    merge_status: MergeStatus | None = None
    merged_by: str | None = None

    def __post_init__(self) -> None:
        self.type = ArtifactType(_enum_value(self.type))
        self.status = ArtifactStatus(_enum_value(self.status))
        if self.merge_status is not None:
            self.merge_status = MergeStatus(_enum_value(self.merge_status))
        elif self.type == ArtifactType.PULL_REQUEST:
            self.merge_status = MergeStatus.PENDING_USER

    @property
    def requires_user_merge(self) -> bool:
        return (
            self.type == ArtifactType.PULL_REQUEST
            and self.status not in {ArtifactStatus.MERGED, ArtifactStatus.CLOSED}
            and self.merge_status == MergeStatus.PENDING_USER
        )


@dataclass
class TaskSessionRef:
    """Opaque executor session reference bound to a task."""

    agent_id: str
    executor: ExecutorType
    execution_location: ExecutionLocation
    issue_id: str
    task_id: str
    workdir: str | None = None
    branch: str | None = None
    executor_session_id: str | None = None

    def __post_init__(self) -> None:
        self.executor = ExecutorType(_enum_value(self.executor))
        self.execution_location = ExecutionLocation(_enum_value(self.execution_location))
