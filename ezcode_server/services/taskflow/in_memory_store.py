"""In-memory Taskflow store for tests and no-database deployments."""

from __future__ import annotations

import threading
import time
from typing import Any

from reuleauxcoder.domain.taskflow.models import (
    DispatchDecisionRecord,
    GoalRecord,
    IssueDraftRecord,
    PlanBriefRecord,
    TaskDraftRecord,
    TaskflowEvent,
    utc_now,
)


class InMemoryTaskflowStore:
    """Thread-safe Taskflow store backed by process memory."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._goals: dict[str, GoalRecord] = {}
        self._briefs_by_goal: dict[str, PlanBriefRecord] = {}
        self._issues: dict[str, IssueDraftRecord] = {}
        self._task_drafts: dict[str, TaskDraftRecord] = {}
        self._decisions: dict[str, list[DispatchDecisionRecord]] = {}
        self._events: dict[str, list[TaskflowEvent]] = {}

    def create_goal(self, goal: GoalRecord) -> GoalRecord:
        with self._cond:
            if goal.id in self._goals:
                raise ValueError(f"taskflow goal already exists: {goal.id}")
            self._goals[goal.id] = goal
            self._events.setdefault(goal.id, [])
            self._cond.notify_all()
            return goal

    def get_goal(self, goal_id: str) -> GoalRecord:
        with self._lock:
            try:
                return self._goals[goal_id]
            except KeyError:
                raise KeyError(f"taskflow goal not found: {goal_id}") from None

    def update_goal(self, goal: GoalRecord) -> GoalRecord:
        with self._cond:
            if goal.id not in self._goals:
                raise KeyError(f"taskflow goal not found: {goal.id}")
            goal.updated_at = utc_now()
            self._goals[goal.id] = goal
            self._cond.notify_all()
            return goal

    def upsert_brief(self, brief: PlanBriefRecord) -> PlanBriefRecord:
        with self._cond:
            if brief.goal_id not in self._goals:
                raise KeyError(f"taskflow goal not found: {brief.goal_id}")
            previous = self._briefs_by_goal.get(brief.goal_id)
            if previous is not None:
                brief.created_at = previous.created_at
                brief.version = previous.version + 1
            brief.updated_at = utc_now()
            self._briefs_by_goal[brief.goal_id] = brief
            self._cond.notify_all()
            return brief

    def get_brief(self, goal_id: str) -> PlanBriefRecord | None:
        with self._lock:
            return self._briefs_by_goal.get(goal_id)

    def create_issue_draft(self, issue: IssueDraftRecord) -> IssueDraftRecord:
        with self._cond:
            if issue.goal_id not in self._goals:
                raise KeyError(f"taskflow goal not found: {issue.goal_id}")
            self._issues[issue.id] = issue
            self._cond.notify_all()
            return issue

    def get_issue_draft(self, issue_draft_id: str) -> IssueDraftRecord:
        with self._lock:
            try:
                return self._issues[issue_draft_id]
            except KeyError:
                raise KeyError(
                    f"taskflow issue draft not found: {issue_draft_id}"
                ) from None

    def list_issue_drafts(self, goal_id: str) -> list[IssueDraftRecord]:
        with self._lock:
            return [
                issue for issue in self._issues.values() if issue.goal_id == goal_id
            ]

    def create_task_draft(self, draft: TaskDraftRecord) -> TaskDraftRecord:
        with self._cond:
            if draft.goal_id not in self._goals:
                raise KeyError(f"taskflow goal not found: {draft.goal_id}")
            if draft.issue_draft_id and draft.issue_draft_id not in self._issues:
                raise KeyError(
                    f"taskflow issue draft not found: {draft.issue_draft_id}"
                )
            self._task_drafts[draft.id] = draft
            self._cond.notify_all()
            return draft

    def get_task_draft(self, draft_id: str) -> TaskDraftRecord:
        with self._lock:
            try:
                return self._task_drafts[draft_id]
            except KeyError:
                raise KeyError(f"taskflow task draft not found: {draft_id}") from None

    def update_task_draft(self, draft: TaskDraftRecord) -> TaskDraftRecord:
        with self._cond:
            if draft.id not in self._task_drafts:
                raise KeyError(f"taskflow task draft not found: {draft.id}")
            draft.updated_at = utc_now()
            self._task_drafts[draft.id] = draft
            self._cond.notify_all()
            return draft

    def list_task_drafts(
        self, goal_id: str, *, issue_draft_id: str | None = None
    ) -> list[TaskDraftRecord]:
        with self._lock:
            drafts = [
                draft for draft in self._task_drafts.values() if draft.goal_id == goal_id
            ]
            if issue_draft_id is not None:
                drafts = [
                    draft for draft in drafts if draft.issue_draft_id == issue_draft_id
                ]
            return drafts

    def append_dispatch_decision(
        self, decision: DispatchDecisionRecord
    ) -> DispatchDecisionRecord:
        with self._cond:
            if decision.task_draft_id not in self._task_drafts:
                raise KeyError(
                    f"taskflow task draft not found: {decision.task_draft_id}"
                )
            self._decisions.setdefault(decision.task_draft_id, []).append(decision)
            self._cond.notify_all()
            return decision

    def list_dispatch_decisions(
        self, task_draft_id: str
    ) -> list[DispatchDecisionRecord]:
        with self._lock:
            return list(self._decisions.get(task_draft_id, []))

    def append_event(
        self, goal_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> TaskflowEvent:
        with self._cond:
            if goal_id not in self._goals:
                raise KeyError(f"taskflow goal not found: {goal_id}")
            events = self._events.setdefault(goal_id, [])
            event = TaskflowEvent(
                goal_id=goal_id,
                seq=len(events) + 1,
                type=event_type,
                payload=dict(payload or {}),
            )
            events.append(event)
            self._cond.notify_all()
            return event

    def list_events(self, goal_id: str, *, after_seq: int = 0) -> list[TaskflowEvent]:
        with self._lock:
            return [
                event
                for event in list(self._events.get(goal_id, []))
                if event.seq > after_seq
            ]

    def wait_events(
        self, goal_id: str, *, after_seq: int = 0, timeout_sec: float = 0.0
    ) -> list[TaskflowEvent]:
        deadline = time.time() + max(0.0, float(timeout_sec or 0.0))
        with self._cond:
            while True:
                events = [
                    event
                    for event in list(self._events.get(goal_id, []))
                    if event.seq > after_seq
                ]
                if events or timeout_sec <= 0:
                    return events
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cond.wait(timeout=remaining)
