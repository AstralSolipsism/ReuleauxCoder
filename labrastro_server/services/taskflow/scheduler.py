"""Deterministic Taskflow capability scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reuleauxcoder.domain.agent_runtime.models import AgentConfig, TaskRecord, TaskStatus
from reuleauxcoder.domain.taskflow.models import TaskDraftRecord


_IN_FLIGHT = {
    TaskStatus.DISPATCHED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_APPROVAL.value,
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _task_status(task: TaskRecord | dict[str, Any]) -> str:
    if isinstance(task, TaskRecord):
        return task.status.value
    return str(task.get("status") or "")


def _task_agent(task: TaskRecord | dict[str, Any]) -> str:
    if isinstance(task, TaskRecord):
        return task.agent_id
    return str(task.get("agent_id") or "")


@dataclass
class TaskflowDispatchResult:
    selected_agent_id: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    filtered: list[dict[str, Any]] = field(default_factory=list)
    score_summary: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    manual_override: bool = False

    @property
    def selected(self) -> bool:
        return bool(self.selected_agent_id)


class TaskflowScheduler:
    """Select an Agent for a confirmed TaskDraft.

    This scheduler is intentionally deterministic and explainable. It filters
    hard constraints first, then ranks remaining candidates by soft capability
    matches, task type hint, current in-flight count, and stable agent id.
    """

    def choose_agent(
        self,
        draft: TaskDraftRecord,
        *,
        runtime_snapshot: dict[str, Any],
        running_tasks: list[TaskRecord | dict[str, Any]] | None = None,
        manual_agent_id: str | None = None,
    ) -> TaskflowDispatchResult:
        raw_agents = _dict(runtime_snapshot.get("agents"))
        raw_profiles = _dict(runtime_snapshot.get("runtime_profiles"))
        agents = {
            agent_id: AgentConfig.from_dict(agent_id, _dict(raw_agent))
            for agent_id, raw_agent in raw_agents.items()
        }
        running_counts = self._running_counts(running_tasks or [])
        requested_manual = str(manual_agent_id or draft.manual_agent_id or "").strip()

        if requested_manual:
            return self._choose_manual(
                requested_manual,
                draft,
                agents=agents,
                raw_agents=raw_agents,
                raw_profiles=raw_profiles,
                running_counts=running_counts,
            )

        candidates: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        for agent_id in sorted(agents):
            agent = agents[agent_id]
            ok, reason = self._passes_hard_filters(
                agent,
                draft,
                raw_profile=_dict(raw_profiles.get(agent.runtime_profile)),
                running_count=running_counts.get(agent.id, 0),
            )
            if not ok:
                filtered.append({"agent_id": agent_id, "reason": reason})
                continue
            candidates.append(
                self._candidate(
                    agent,
                    draft,
                    raw_agent=_dict(raw_agents.get(agent_id)),
                    running_count=running_counts.get(agent_id, 0),
                )
            )

        if not candidates:
            return TaskflowDispatchResult(
                selected_agent_id=None,
                candidates=[],
                filtered=filtered,
                reason="no_candidate",
            )

        ranked = sorted(
            candidates,
            key=lambda item: (
                -int(item.get("soft_match_count") or 0),
                -int(item.get("task_type_match") or 0),
                int(item.get("running_count") or 0),
                str(item.get("agent_id") or ""),
            ),
        )
        selected = ranked[0]
        return TaskflowDispatchResult(
            selected_agent_id=str(selected["agent_id"]),
            candidates=ranked,
            filtered=filtered,
            score_summary={
                "ordering": [
                    "soft_match_count desc",
                    "task_type_match desc",
                    "running_count asc",
                    "agent_id asc",
                ],
                "selected": selected,
            },
            reason="deterministic_capability_match",
        )

    def _choose_manual(
        self,
        agent_id: str,
        draft: TaskDraftRecord,
        *,
        agents: dict[str, AgentConfig],
        raw_agents: dict[str, Any],
        raw_profiles: dict[str, Any],
        running_counts: dict[str, int],
    ) -> TaskflowDispatchResult:
        agent = agents.get(agent_id)
        if agent is None:
            return TaskflowDispatchResult(
                filtered=[{"agent_id": agent_id, "reason": "agent_not_found"}],
                reason="manual_agent_not_found",
                manual_override=True,
            )
        ok, reason = self._passes_hard_filters(
            agent,
            draft,
            raw_profile=_dict(raw_profiles.get(agent.runtime_profile)),
            running_count=running_counts.get(agent.id, 0),
        )
        candidate = self._candidate(
            agent,
            draft,
            raw_agent=_dict(raw_agents.get(agent_id)),
            running_count=running_counts.get(agent.id, 0),
        )
        if not ok:
            return TaskflowDispatchResult(
                selected_agent_id=None,
                candidates=[candidate],
                filtered=[{"agent_id": agent_id, "reason": reason}],
                reason=f"manual_agent_rejected:{reason}",
                manual_override=True,
            )
        return TaskflowDispatchResult(
            selected_agent_id=agent_id,
            candidates=[candidate],
            filtered=[],
            score_summary={"selected": candidate},
            reason="manual_override",
            manual_override=True,
        )

    def _passes_hard_filters(
        self,
        agent: AgentConfig,
        draft: TaskDraftRecord,
        *,
        raw_profile: dict[str, Any],
        running_count: int,
    ) -> tuple[bool, str]:
        if not agent.runtime_profile:
            return False, "missing_runtime_profile"
        if not raw_profile:
            return False, "runtime_profile_not_found"
        required = set(draft.required_capabilities)
        missing = sorted(required - set(agent.capabilities))
        if missing:
            return False, "missing_required_capabilities:" + ",".join(missing)
        if draft.execution_location:
            profile_location = str(raw_profile.get("execution_location") or "")
            if profile_location and profile_location != draft.execution_location:
                return False, "execution_location_mismatch"
        if (
            agent.max_concurrent_tasks is not None
            and running_count >= agent.max_concurrent_tasks
        ):
            return False, "agent_concurrency_full"
        return True, ""

    def _candidate(
        self,
        agent: AgentConfig,
        draft: TaskDraftRecord,
        *,
        raw_agent: dict[str, Any],
        running_count: int,
    ) -> dict[str, Any]:
        agent_signals = set(agent.capabilities)
        agent_signals.update(_string_list(raw_agent.get("specialties")))
        agent_signals.update(_string_list(raw_agent.get("workflows")))
        agent_signals.update(_string_list(raw_agent.get("task_types")))
        soft_matches = sorted(set(draft.preferred_capabilities) & agent_signals)
        task_type_match = 0
        if draft.task_type and draft.task_type in agent_signals:
            task_type_match = 1
        return {
            "agent_id": agent.id,
            "capabilities": list(agent.capabilities),
            "matched_preferred_capabilities": soft_matches,
            "soft_match_count": len(soft_matches),
            "task_type": draft.task_type,
            "task_type_match": task_type_match,
            "running_count": running_count,
            "max_concurrent_tasks": agent.max_concurrent_tasks,
            "runtime_profile": agent.runtime_profile,
        }

    def _running_counts(
        self, running_tasks: list[TaskRecord | dict[str, Any]]
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in running_tasks:
            if _task_status(task) not in _IN_FLIGHT:
                continue
            agent_id = _task_agent(task)
            if not agent_id:
                continue
            counts[agent_id] = counts.get(agent_id, 0) + 1
        return counts


__all__ = ["TaskflowDispatchResult", "TaskflowScheduler"]
