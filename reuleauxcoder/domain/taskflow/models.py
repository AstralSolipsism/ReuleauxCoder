"""Domain models for Taskflow planning and dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum_value(value: Enum | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


class GoalStatus(str, Enum):
    PLANNING = "planning"
    READY = "ready"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class TaskDraftStatus(str, Enum):
    PROPOSED = "proposed"
    READY = "ready"
    CONFIRMED = "confirmed"
    DISPATCHED = "dispatched"
    NEEDS_ASSIGNMENT = "needs_assignment"
    CANCELLED = "cancelled"


class DispatchDecisionStatus(str, Enum):
    SELECTED = "selected"
    NEEDS_ASSIGNMENT = "needs_assignment"
    MANUAL_OVERRIDE = "manual_override"
    REJECTED = "rejected"


@dataclass
class DecisionPoint:
    id: str
    question: str
    options: list[str] = field(default_factory=list)
    recommendation: str = ""
    status: str = "open"
    answer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionPoint":
        return cls(
            id=str(data.get("id") or ""),
            question=str(data.get("question") or ""),
            options=_string_list(data.get("options")),
            recommendation=str(data.get("recommendation") or ""),
            status=str(data.get("status") or "open"),
            answer=str(data["answer"]) if data.get("answer") is not None else None,
            metadata=_dict(data.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "recommendation": self.recommendation,
            "status": self.status,
            "answer": self.answer,
            "metadata": dict(self.metadata),
        }


@dataclass
class GoalRecord:
    id: str
    title: str
    prompt: str
    status: GoalStatus | str = GoalStatus.PLANNING
    session_id: str | None = None
    peer_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.status = GoalStatus(_enum_value(self.status))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalRecord":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            prompt=str(data.get("prompt") or ""),
            status=str(data.get("status") or GoalStatus.PLANNING.value),
            session_id=(
                str(data["session_id"]) if data.get("session_id") is not None else None
            ),
            peer_id=str(data["peer_id"]) if data.get("peer_id") is not None else None,
            metadata=_dict(data.get("metadata")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "status": self.status.value,
            "session_id": self.session_id,
            "peer_id": self.peer_id,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PlanBriefRecord:
    id: str
    goal_id: str
    summary: str = ""
    decision_points: list[DecisionPoint] = field(default_factory=list)
    status: PlanStatus | str = PlanStatus.DRAFT
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.status = PlanStatus(_enum_value(self.status))
        self.decision_points = [
            point
            if isinstance(point, DecisionPoint)
            else DecisionPoint.from_dict(_dict(point))
            for point in self.decision_points
        ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanBriefRecord":
        return cls(
            id=str(data.get("id") or ""),
            goal_id=str(data.get("goal_id") or ""),
            summary=str(data.get("summary") or ""),
            decision_points=[
                DecisionPoint.from_dict(_dict(point))
                for point in _list(data.get("decision_points"))
            ],
            status=str(data.get("status") or PlanStatus.DRAFT.value),
            version=int(data.get("version") or 1),
            metadata=_dict(data.get("metadata")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "summary": self.summary,
            "decision_points": [point.to_dict() for point in self.decision_points],
            "status": self.status.value,
            "version": self.version,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class IssueDraftRecord:
    id: str
    goal_id: str
    title: str
    description: str = ""
    status: str = "proposed"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IssueDraftRecord":
        return cls(
            id=str(data.get("id") or ""),
            goal_id=str(data.get("goal_id") or ""),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            status=str(data.get("status") or "proposed"),
            metadata=_dict(data.get("metadata")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class TaskDraftRecord:
    id: str
    goal_id: str
    title: str
    prompt: str
    issue_draft_id: str | None = None
    status: TaskDraftStatus | str = TaskDraftStatus.PROPOSED
    required_capabilities: list[str] = field(default_factory=list)
    preferred_capabilities: list[str] = field(default_factory=list)
    task_type: str | None = None
    workspace_root: str | None = None
    repo_url: str | None = None
    execution_location: str | None = None
    manual_agent_id: str | None = None
    runtime_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.status = TaskDraftStatus(_enum_value(self.status))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskDraftRecord":
        return cls(
            id=str(data.get("id") or ""),
            goal_id=str(data.get("goal_id") or ""),
            title=str(data.get("title") or ""),
            prompt=str(data.get("prompt") or ""),
            issue_draft_id=(
                str(data["issue_draft_id"])
                if data.get("issue_draft_id") is not None
                else None
            ),
            status=str(data.get("status") or TaskDraftStatus.PROPOSED.value),
            required_capabilities=_string_list(data.get("required_capabilities")),
            preferred_capabilities=_string_list(data.get("preferred_capabilities")),
            task_type=str(data["task_type"]) if data.get("task_type") is not None else None,
            workspace_root=(
                str(data["workspace_root"])
                if data.get("workspace_root") is not None
                else None
            ),
            repo_url=str(data["repo_url"]) if data.get("repo_url") is not None else None,
            execution_location=(
                str(data["execution_location"])
                if data.get("execution_location") is not None
                else None
            ),
            manual_agent_id=(
                str(data["manual_agent_id"])
                if data.get("manual_agent_id") is not None
                else None
            ),
            runtime_task_id=(
                str(data["runtime_task_id"])
                if data.get("runtime_task_id") is not None
                else None
            ),
            metadata=_dict(data.get("metadata")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "issue_draft_id": self.issue_draft_id,
            "title": self.title,
            "prompt": self.prompt,
            "status": self.status.value,
            "required_capabilities": list(self.required_capabilities),
            "preferred_capabilities": list(self.preferred_capabilities),
            "task_type": self.task_type,
            "workspace_root": self.workspace_root,
            "repo_url": self.repo_url,
            "execution_location": self.execution_location,
            "manual_agent_id": self.manual_agent_id,
            "runtime_task_id": self.runtime_task_id,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class DispatchDecisionRecord:
    id: str
    task_draft_id: str
    status: DispatchDecisionStatus | str
    selected_agent_id: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    filtered: list[dict[str, Any]] = field(default_factory=list)
    score_summary: dict[str, Any] = field(default_factory=dict)
    manual_override: bool = False
    reason: str = ""
    runtime_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.status = DispatchDecisionStatus(_enum_value(self.status))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DispatchDecisionRecord":
        return cls(
            id=str(data.get("id") or ""),
            task_draft_id=str(data.get("task_draft_id") or ""),
            status=str(data.get("status") or DispatchDecisionStatus.REJECTED.value),
            selected_agent_id=(
                str(data["selected_agent_id"])
                if data.get("selected_agent_id") is not None
                else None
            ),
            candidates=[_dict(item) for item in _list(data.get("candidates"))],
            filtered=[_dict(item) for item in _list(data.get("filtered"))],
            score_summary=_dict(data.get("score_summary")),
            manual_override=bool(data.get("manual_override", False)),
            reason=str(data.get("reason") or ""),
            runtime_task_id=(
                str(data["runtime_task_id"])
                if data.get("runtime_task_id") is not None
                else None
            ),
            metadata=_dict(data.get("metadata")),
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_draft_id": self.task_draft_id,
            "status": self.status.value,
            "selected_agent_id": self.selected_agent_id,
            "candidates": [dict(item) for item in self.candidates],
            "filtered": [dict(item) for item in self.filtered],
            "score_summary": dict(self.score_summary),
            "manual_override": self.manual_override,
            "reason": self.reason,
            "runtime_task_id": self.runtime_task_id,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass
class TaskflowEvent:
    goal_id: str
    seq: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskflowEvent":
        return cls(
            goal_id=str(data.get("goal_id") or ""),
            seq=int(data.get("seq") or 0),
            type=str(data.get("type") or ""),
            payload=_dict(data.get("payload")),
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "seq": self.seq,
            "type": self.type,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }
