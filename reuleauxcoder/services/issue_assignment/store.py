"""Storage protocol for Issue Assignment and Mention Agent state."""

from __future__ import annotations

from typing import Any, Protocol

from reuleauxcoder.domain.issue_assignment.models import (
    AssignmentRecord,
    IssueAssignmentEvent,
    IssueRecord,
    MentionRecord,
)


class IssueAssignmentStore(Protocol):
    def create_issue(self, issue: IssueRecord) -> IssueRecord: ...

    def get_issue(self, issue_id: str) -> IssueRecord: ...

    def update_issue(self, issue: IssueRecord) -> IssueRecord: ...

    def list_issues(self, peer_id: str | None = None) -> list[IssueRecord]: ...

    def create_assignment(self, assignment: AssignmentRecord) -> AssignmentRecord: ...

    def get_assignment(self, assignment_id: str) -> AssignmentRecord: ...

    def update_assignment(self, assignment: AssignmentRecord) -> AssignmentRecord: ...

    def list_assignments(self, issue_id: str) -> list[AssignmentRecord]: ...

    def create_mention(self, mention: MentionRecord) -> MentionRecord: ...

    def get_mention(self, mention_id: str) -> MentionRecord: ...

    def update_mention(self, mention: MentionRecord) -> MentionRecord: ...

    def list_mentions(
        self, *, peer_id: str | None = None, issue_id: str | None = None
    ) -> list[MentionRecord]: ...

    def append_event(
        self,
        scope: str,
        scope_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> IssueAssignmentEvent: ...

    def list_events(
        self, scope: str, scope_id: str, *, after_seq: int = 0
    ) -> list[IssueAssignmentEvent]: ...

    def wait_events(
        self,
        scope: str,
        scope_id: str,
        *,
        after_seq: int = 0,
        timeout_sec: float = 0.0,
    ) -> list[IssueAssignmentEvent]: ...
