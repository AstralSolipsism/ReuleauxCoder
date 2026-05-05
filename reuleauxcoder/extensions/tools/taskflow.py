"""Taskflow-only planning tool."""

from __future__ import annotations

import json
from typing import Any

from reuleauxcoder.extensions.tools.base import Tool
from ezcode_server.services.taskflow.service import TaskflowService


class TaskflowPlanningTool(Tool):
    """Tool injected only when workflow_mode=taskflow."""

    name = "taskflow_update"
    description = (
        "Record Taskflow planning state for the current long-running goal: "
        "brief, decision points, issue drafts, task drafts, ready state, or "
        "cancellation. Use only after discussing the goal with the user. Final "
        "confirmation must come from the user confirmation API, not this tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "record_brief",
                    "propose_issue",
                    "propose_task",
                    "mark_ready",
                    "cancel_goal",
                ],
            },
            "goal_id": {"type": "string"},
            "summary": {"type": "string"},
            "decision_points": {
                "type": "array",
                "items": {"type": "object"},
            },
            "issue_drafts": {"type": "array", "items": {"type": "object"}},
            "task_drafts": {"type": "array", "items": {"type": "object"}},
            "issue_draft_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "required_capabilities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "preferred_capabilities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "task_type": {"type": "string"},
            "workspace_root": {"type": "string"},
            "repo_url": {"type": "string"},
            "execution_location": {"type": "string"},
            "manual_agent_id": {"type": "string"},
            "metadata": {"type": "object"},
            "reason": {"type": "string"},
        },
        "required": ["operation"],
    }

    def __init__(self, service: TaskflowService, *, goal_id: str | None = None):
        super().__init__(backend=None)
        self.service = service
        self.goal_id = goal_id

    def execute(self, operation: str, **kwargs: Any) -> str:
        goal_id = str(kwargs.get("goal_id") or self.goal_id or "").strip()
        if not goal_id:
            return "Error: goal_id is required for taskflow_update"
        try:
            if operation == "record_brief":
                brief = self.service.record_brief(
                    goal_id,
                    summary=str(kwargs.get("summary") or ""),
                    decision_points=self._list(kwargs.get("decision_points")),
                    issue_drafts=self._list(kwargs.get("issue_drafts")),
                    task_drafts=self._list(kwargs.get("task_drafts")),
                    ready=False,
                    metadata=self._dict(kwargs.get("metadata")),
                )
                return self._json({"ok": True, "brief": brief.to_dict()})
            if operation == "propose_issue":
                issue = self.service.create_issue_draft(
                    goal_id,
                    title=str(kwargs.get("title") or ""),
                    description=str(kwargs.get("description") or ""),
                    metadata=self._dict(kwargs.get("metadata")),
                )
                return self._json({"ok": True, "issue_draft": issue.to_dict()})
            if operation == "propose_task":
                draft = self.service.create_task_draft(
                    goal_id,
                    issue_draft_id=self._optional(kwargs.get("issue_draft_id")),
                    title=str(kwargs.get("title") or ""),
                    prompt=str(kwargs.get("prompt") or ""),
                    required_capabilities=self._strings(
                        kwargs.get("required_capabilities")
                    ),
                    preferred_capabilities=self._strings(
                        kwargs.get("preferred_capabilities")
                    ),
                    task_type=self._optional(kwargs.get("task_type")),
                    workspace_root=self._optional(kwargs.get("workspace_root")),
                    repo_url=self._optional(kwargs.get("repo_url")),
                    execution_location=self._optional(kwargs.get("execution_location")),
                    manual_agent_id=self._optional(kwargs.get("manual_agent_id")),
                    metadata=self._dict(kwargs.get("metadata")),
                )
                return self._json({"ok": True, "task_draft": draft.to_dict()})
            if operation == "mark_ready":
                brief = self.service.record_brief(
                    goal_id,
                    summary=str(kwargs.get("summary") or ""),
                    decision_points=self._list(kwargs.get("decision_points")),
                    issue_drafts=self._list(kwargs.get("issue_drafts")),
                    task_drafts=self._list(kwargs.get("task_drafts")),
                    ready=True,
                    metadata=self._dict(kwargs.get("metadata")),
                )
                return self._json({"ok": True, "brief": brief.to_dict()})
            if operation == "cancel_goal":
                goal = self.service.cancel_goal(
                    goal_id, reason=str(kwargs.get("reason") or "user_cancelled")
                )
                return self._json({"ok": True, "goal": goal.to_dict()})
            return f"Error: unknown Taskflow operation: {operation}"
        except Exception as exc:
            return f"Error: {exc}"

    def _json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _dict(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _list(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _strings(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if value is None or value == "":
            return []
        return [str(value)]

    def _optional(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
