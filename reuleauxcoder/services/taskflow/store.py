"""Storage protocol for Taskflow planning and dispatch state."""

from __future__ import annotations

from typing import Any, Protocol

from reuleauxcoder.domain.taskflow.models import (
    DispatchDecisionRecord,
    GoalRecord,
    IssueDraftRecord,
    PlanBriefRecord,
    TaskDraftRecord,
    TaskflowEvent,
)


class TaskflowStore(Protocol):
    def create_goal(self, goal: GoalRecord) -> GoalRecord: ...

    def get_goal(self, goal_id: str) -> GoalRecord: ...

    def update_goal(self, goal: GoalRecord) -> GoalRecord: ...

    def upsert_brief(self, brief: PlanBriefRecord) -> PlanBriefRecord: ...

    def get_brief(self, goal_id: str) -> PlanBriefRecord | None: ...

    def create_issue_draft(self, issue: IssueDraftRecord) -> IssueDraftRecord: ...

    def get_issue_draft(self, issue_draft_id: str) -> IssueDraftRecord: ...

    def list_issue_drafts(self, goal_id: str) -> list[IssueDraftRecord]: ...

    def create_task_draft(self, draft: TaskDraftRecord) -> TaskDraftRecord: ...

    def get_task_draft(self, draft_id: str) -> TaskDraftRecord: ...

    def update_task_draft(self, draft: TaskDraftRecord) -> TaskDraftRecord: ...

    def list_task_drafts(
        self, goal_id: str, *, issue_draft_id: str | None = None
    ) -> list[TaskDraftRecord]: ...

    def append_dispatch_decision(
        self, decision: DispatchDecisionRecord
    ) -> DispatchDecisionRecord: ...

    def list_dispatch_decisions(
        self, task_draft_id: str
    ) -> list[DispatchDecisionRecord]: ...

    def append_event(
        self, goal_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> TaskflowEvent: ...

    def list_events(self, goal_id: str, *, after_seq: int = 0) -> list[TaskflowEvent]: ...

    def wait_events(
        self, goal_id: str, *, after_seq: int = 0, timeout_sec: float = 0.0
    ) -> list[TaskflowEvent]: ...
