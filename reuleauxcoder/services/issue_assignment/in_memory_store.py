"""In-memory Issue Assignment store for tests and no-database deployments."""

from __future__ import annotations

import threading
import time
from typing import Any

from reuleauxcoder.domain.issue_assignment.models import (
    AssignmentRecord,
    IssueAssignmentEvent,
    IssueRecord,
    MentionRecord,
    utc_now,
)


class InMemoryIssueAssignmentStore:
    """Thread-safe Issue/Assignment/Mention store backed by process memory."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._issues: dict[str, IssueRecord] = {}
        self._assignments: dict[str, AssignmentRecord] = {}
        self._mentions: dict[str, MentionRecord] = {}
        self._events: dict[tuple[str, str], list[IssueAssignmentEvent]] = {}

    def create_issue(self, issue: IssueRecord) -> IssueRecord:
        with self._cond:
            if issue.id in self._issues:
                raise ValueError(f"issue already exists: {issue.id}")
            self._issues[issue.id] = issue
            self._cond.notify_all()
            return issue

    def get_issue(self, issue_id: str) -> IssueRecord:
        with self._lock:
            try:
                return self._issues[issue_id]
            except KeyError:
                raise KeyError(f"issue not found: {issue_id}") from None

    def update_issue(self, issue: IssueRecord) -> IssueRecord:
        with self._cond:
            if issue.id not in self._issues:
                raise KeyError(f"issue not found: {issue.id}")
            issue.updated_at = utc_now()
            self._issues[issue.id] = issue
            self._cond.notify_all()
            return issue

    def list_issues(self, peer_id: str | None = None) -> list[IssueRecord]:
        with self._lock:
            issues = list(self._issues.values())
            if peer_id is not None:
                issues = [issue for issue in issues if issue.peer_id == peer_id]
            return sorted(issues, key=lambda issue: issue.created_at)

    def create_assignment(self, assignment: AssignmentRecord) -> AssignmentRecord:
        with self._cond:
            if assignment.issue_id not in self._issues:
                raise KeyError(f"issue not found: {assignment.issue_id}")
            if assignment.id in self._assignments:
                raise ValueError(f"assignment already exists: {assignment.id}")
            self._assignments[assignment.id] = assignment
            self._cond.notify_all()
            return assignment

    def get_assignment(self, assignment_id: str) -> AssignmentRecord:
        with self._lock:
            try:
                return self._assignments[assignment_id]
            except KeyError:
                raise KeyError(f"assignment not found: {assignment_id}") from None

    def update_assignment(self, assignment: AssignmentRecord) -> AssignmentRecord:
        with self._cond:
            if assignment.id not in self._assignments:
                raise KeyError(f"assignment not found: {assignment.id}")
            assignment.updated_at = utc_now()
            self._assignments[assignment.id] = assignment
            self._cond.notify_all()
            return assignment

    def list_assignments(self, issue_id: str) -> list[AssignmentRecord]:
        with self._lock:
            return sorted(
                [
                    assignment
                    for assignment in self._assignments.values()
                    if assignment.issue_id == issue_id
                ],
                key=lambda assignment: assignment.created_at,
            )

    def create_mention(self, mention: MentionRecord) -> MentionRecord:
        with self._cond:
            if mention.issue_id and mention.issue_id not in self._issues:
                raise KeyError(f"issue not found: {mention.issue_id}")
            if mention.assignment_id and mention.assignment_id not in self._assignments:
                raise KeyError(f"assignment not found: {mention.assignment_id}")
            if mention.id in self._mentions:
                raise ValueError(f"mention already exists: {mention.id}")
            self._mentions[mention.id] = mention
            self._cond.notify_all()
            return mention

    def get_mention(self, mention_id: str) -> MentionRecord:
        with self._lock:
            try:
                return self._mentions[mention_id]
            except KeyError:
                raise KeyError(f"mention not found: {mention_id}") from None

    def update_mention(self, mention: MentionRecord) -> MentionRecord:
        with self._cond:
            if mention.id not in self._mentions:
                raise KeyError(f"mention not found: {mention.id}")
            mention.updated_at = utc_now()
            self._mentions[mention.id] = mention
            self._cond.notify_all()
            return mention

    def list_mentions(
        self, *, peer_id: str | None = None, issue_id: str | None = None
    ) -> list[MentionRecord]:
        with self._lock:
            mentions = list(self._mentions.values())
            if peer_id is not None:
                mentions = [mention for mention in mentions if mention.peer_id == peer_id]
            if issue_id is not None:
                mentions = [
                    mention for mention in mentions if mention.issue_id == issue_id
                ]
            return sorted(mentions, key=lambda mention: mention.created_at)

    def append_event(
        self,
        scope: str,
        scope_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> IssueAssignmentEvent:
        with self._cond:
            key = (scope, scope_id)
            events = self._events.setdefault(key, [])
            event = IssueAssignmentEvent(
                scope=scope,
                scope_id=scope_id,
                seq=len(events) + 1,
                type=event_type,
                payload=dict(payload or {}),
            )
            events.append(event)
            self._cond.notify_all()
            return event

    def list_events(
        self, scope: str, scope_id: str, *, after_seq: int = 0
    ) -> list[IssueAssignmentEvent]:
        with self._lock:
            return [
                event
                for event in list(self._events.get((scope, scope_id), []))
                if event.seq > after_seq
            ]

    def wait_events(
        self,
        scope: str,
        scope_id: str,
        *,
        after_seq: int = 0,
        timeout_sec: float = 0.0,
    ) -> list[IssueAssignmentEvent]:
        deadline = time.time() + max(0.0, float(timeout_sec or 0.0))
        with self._cond:
            while True:
                events = self.list_events(scope, scope_id, after_seq=after_seq)
                if events or timeout_sec <= 0:
                    return events
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cond.wait(timeout=remaining)
