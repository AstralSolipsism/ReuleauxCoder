from __future__ import annotations

from reuleauxcoder.domain.taskflow.models import TaskDraftRecord
from reuleauxcoder.services.taskflow.scheduler import TaskflowScheduler


def _snapshot() -> dict:
    return {
        "runtime_profiles": {
            "docs_profile": {
                "executor": "fake",
                "execution_location": "remote_server",
            },
            "code_profile": {
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
            "coder": {
                "runtime_profile": "code_profile",
                "capabilities": ["write_code", "review_code", "code"],
            },
            "designer": {
                "runtime_profile": "design_profile",
                "capabilities": ["design", "research"],
            },
        },
    }


def test_scheduler_filters_hard_constraints_and_ranks_soft_signals() -> None:
    draft = TaskDraftRecord(
        id="draft-1",
        goal_id="goal-1",
        title="docs",
        prompt="write docs",
        required_capabilities=["research"],
        preferred_capabilities=["docs", "write_docs"],
        task_type="docs",
    )

    result = TaskflowScheduler().choose_agent(
        draft,
        runtime_snapshot=_snapshot(),
        running_tasks=[{"agent_id": "docs", "status": "running"}],
    )

    assert result.selected_agent_id == "docs"
    assert result.candidates[0]["matched_preferred_capabilities"] == [
        "docs",
        "write_docs",
    ]
    assert {item["agent_id"] for item in result.filtered} == {"coder"}


def test_manual_assignment_has_priority_but_keeps_hard_filters() -> None:
    draft = TaskDraftRecord(
        id="draft-1",
        goal_id="goal-1",
        title="docs",
        prompt="write docs",
        required_capabilities=["write_docs"],
        manual_agent_id="designer",
    )

    rejected = TaskflowScheduler().choose_agent(
        draft,
        runtime_snapshot=_snapshot(),
    )
    assert rejected.selected_agent_id is None
    assert rejected.manual_override is True
    assert rejected.filtered[0]["reason"].startswith("missing_required_capabilities")

    draft.manual_agent_id = "docs"
    selected = TaskflowScheduler().choose_agent(
        draft,
        runtime_snapshot=_snapshot(),
    )
    assert selected.selected_agent_id == "docs"
    assert selected.manual_override is True


def test_scheduler_returns_no_candidate_when_no_agent_matches() -> None:
    draft = TaskDraftRecord(
        id="draft-1",
        goal_id="goal-1",
        title="secure",
        prompt="needs permission",
        required_capabilities=["use_secret_vault"],
    )

    result = TaskflowScheduler().choose_agent(draft, runtime_snapshot=_snapshot())

    assert result.selected_agent_id is None
    assert result.reason == "no_candidate"
