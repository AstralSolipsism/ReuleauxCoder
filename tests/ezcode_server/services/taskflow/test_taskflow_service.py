from __future__ import annotations

import pytest

from reuleauxcoder.domain.taskflow.models import TaskDraftStatus
from ezcode_server.services.agent_runtime.control_plane import AgentRuntimeControlPlane
from ezcode_server.services.taskflow.service import TaskflowService


def _runtime() -> AgentRuntimeControlPlane:
    return AgentRuntimeControlPlane(
        runtime_snapshot={
            "runtime_profiles": {
                "docs_profile": {
                    "executor": "fake",
                    "execution_location": "remote_server",
                },
                "design_profile": {
                    "executor": "fake",
                    "execution_location": "remote_server",
                },
            },
            "agents": {
                "docs": {
                    "runtime_profile": "docs_profile",
                    "capabilities": ["write_docs", "research", "docs"],
                    "max_concurrent_tasks": 2,
                },
                "design": {
                    "runtime_profile": "design_profile",
                    "capabilities": ["design"],
                },
            },
        }
    )


def test_taskflow_records_plan_and_does_not_create_runtime_task_before_confirm() -> None:
    runtime = _runtime()
    service = TaskflowService(runtime_control_plane=runtime)
    goal = service.create_goal(title="Travel plan", prompt="Plan a trip")

    service.record_brief(
        goal.id,
        summary="Prepare a short travel plan.",
        decision_points=[
            {
                "question": "Budget?",
                "options": ["low", "medium"],
                "recommendation": "medium",
            }
        ],
        issue_drafts=[
            {
                "title": "Research itinerary",
                "description": "Find route and hotels",
                "task_drafts": [
                    {
                        "title": "Draft itinerary",
                        "prompt": "Draft the itinerary",
                        "required_capabilities": ["research"],
                        "preferred_capabilities": ["docs"],
                        "task_type": "docs",
                    }
                ],
            }
        ],
        ready=True,
    )

    detail = service.load_goal_detail(goal.id)
    assert detail["goal"]["status"] == "ready"
    assert len(detail["issue_drafts"]) == 1
    assert len(detail["task_drafts"]) == 1
    assert runtime.list_tasks() == []

    service.confirm_goal(goal.id)
    confirmed = service.load_goal_detail(goal.id)
    assert confirmed["goal"]["status"] == "confirmed"
    assert confirmed["task_drafts"][0]["status"] == "confirmed"
    assert runtime.list_tasks() == []


def test_dispatch_creates_runtime_task_with_selected_agent_and_audit_record() -> None:
    runtime = _runtime()
    service = TaskflowService(runtime_control_plane=runtime)
    goal = service.create_goal(title="Docs", prompt="Write docs")
    draft = service.create_task_draft(
        goal.id,
        title="Write docs",
        prompt="Write the documentation",
        required_capabilities=["research"],
        preferred_capabilities=["docs"],
        task_type="docs",
    )
    service.confirm_goal(goal.id)

    decision = service.dispatch_task_draft(draft.id)

    assert decision.selected_agent_id == "docs"
    assert decision.runtime_task_id is not None
    task = runtime.get_task(decision.runtime_task_id)
    assert task.agent_id == "docs"
    assert task.metadata["taskflow_goal_id"] == goal.id
    assert service.list_dispatch_decisions(draft.id)[0].selected_agent_id == "docs"


def test_dispatch_without_candidate_marks_task_draft_needs_assignment() -> None:
    runtime = _runtime()
    service = TaskflowService(runtime_control_plane=runtime)
    goal = service.create_goal(title="Secure", prompt="Needs unavailable capability")
    draft = service.create_task_draft(
        goal.id,
        title="Use secret vault",
        prompt="Use the vault",
        required_capabilities=["use_secret_vault"],
    )
    service.confirm_goal(goal.id)

    decision = service.dispatch_task_draft(draft.id)
    updated = service.store.get_task_draft(draft.id)

    assert decision.selected_agent_id is None
    assert updated.status == TaskDraftStatus.NEEDS_ASSIGNMENT
    assert runtime.list_tasks() == []


def test_peer_ownership_blocks_cross_peer_taskflow_access_and_dispatch() -> None:
    runtime = _runtime()
    service = TaskflowService(runtime_control_plane=runtime)
    goal = service.create_goal(title="Owned", prompt="owned", peer_id="peer-a")
    draft = service.create_task_draft(
        goal.id,
        title="Write docs",
        prompt="Write the documentation",
        required_capabilities=["docs"],
        peer_id="peer-a",
    )
    service.confirm_goal(goal.id, peer_id="peer-a")

    with pytest.raises(PermissionError):
        service.load_goal_detail(goal.id, peer_id="peer-b")
    with pytest.raises(PermissionError):
        service.dispatch_task_draft(draft.id, peer_id="peer-b")


def test_dispatch_records_source_metadata_and_runtime_dispatch_source() -> None:
    runtime = _runtime()
    service = TaskflowService(runtime_control_plane=runtime)
    goal = service.create_goal(title="Docs", prompt="Write docs")
    draft = service.create_task_draft(
        goal.id,
        title="Write docs",
        prompt="Write the documentation",
        required_capabilities=["docs"],
    )
    service.confirm_goal(goal.id)

    decision = service.dispatch_task_draft(
        draft.id, source="mention", metadata={"mention_id": "mention-1"}
    )

    assert decision.metadata["source"] == "mention"
    assert decision.metadata["mention_id"] == "mention-1"
    task = runtime.get_task(decision.runtime_task_id or "")
    assert task.metadata["dispatch_source"] == "mention"
