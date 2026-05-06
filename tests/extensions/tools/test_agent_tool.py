from unittest import mock

from reuleauxcoder.extensions.tools.builtin.agent import AgentTool


class _SubagentManagerStub:
    default_max_rounds = 50

    def __init__(self) -> None:
        self.sync_calls: list[dict] = []
        self.background_calls: list[dict] = []

    def is_valid_mode(self, mode: str) -> bool:
        return mode in {"explore", "execute", "verify"}

    def run_sync(self, **kwargs) -> str:
        self.sync_calls.append(kwargs)
        return f"sync:{kwargs['task']}"

    def submit_background(self, **kwargs) -> str:
        self.background_calls.append(kwargs)
        return f"job-{len(self.background_calls)}"


def _tool() -> AgentTool:
    tool = AgentTool()
    tool._parent_agent = object()
    return tool


def test_agent_tool_schema_requires_tasks_only() -> None:
    properties = AgentTool.parameters["properties"]

    assert "task" not in properties
    assert "tasks" in properties
    assert AgentTool.parameters["required"] == ["tasks"]


def test_agent_tool_rejects_legacy_task_argument() -> None:
    tool = AgentTool()

    assert (
        tool.preflight_validate(task="old")
        == "Error: 'tasks' must be a non-empty list of task strings."
    )


def test_agent_tool_rejects_empty_tasks() -> None:
    tool = AgentTool()

    assert (
        tool.preflight_validate(tasks=[])
        == "Error: 'tasks' must be a non-empty list of task strings."
    )
    assert (
        tool.preflight_validate(tasks=[" ", ""])
        == "Error: 'tasks' must be a non-empty list of task strings."
    )
    assert (
        tool.preflight_validate(tasks="scan")
        == "Error: 'tasks' must be a non-empty list of task strings."
    )


def test_agent_tool_syncs_single_task() -> None:
    manager = _SubagentManagerStub()
    tool = _tool()

    with mock.patch(
        "reuleauxcoder.extensions.tools.builtin.agent.get_subagent_manager",
        return_value=manager,
    ):
        result = tool.execute(
            tasks=[" scan "],
            mode="verify",
            run_in_background=False,
            max_rounds=3,
            timeout_seconds=4,
            model="main",
        )

    assert result == "sync:scan"
    assert manager.sync_calls == [
        {
            "parent_agent": tool._parent_agent,
            "task": "scan",
            "mode": "verify",
            "max_rounds": 3,
            "timeout_seconds": 4,
            "model_profile_name": "main",
        }
    ]
    assert manager.background_calls == []


def test_agent_tool_rejects_background_non_explore_task() -> None:
    tool = _tool()

    assert (
        tool.preflight_validate(
            tasks=["scan"],
            mode="execute",
            run_in_background=True,
        )
        == "Error: background sub-agent jobs require mode='explore'."
    )


def test_agent_tool_starts_single_background_task() -> None:
    manager = _SubagentManagerStub()
    tool = _tool()

    with mock.patch(
        "reuleauxcoder.extensions.tools.builtin.agent.get_subagent_manager",
        return_value=manager,
    ):
        result = tool.execute(
            tasks=["scan"],
            mode="explore",
            run_in_background=True,
            parallel_explore=2,
        )

    assert result == "Sub-agent job started in background: job-1"
    assert [call["task"] for call in manager.background_calls] == ["scan"]
    assert manager.background_calls[0]["mode"] == "explore"
    assert manager.background_calls[0]["parallel_explore"] == 2
    assert manager.sync_calls == []


def test_agent_tool_starts_batch_background_tasks_in_explore_mode() -> None:
    manager = _SubagentManagerStub()
    tool = _tool()

    with mock.patch(
        "reuleauxcoder.extensions.tools.builtin.agent.get_subagent_manager",
        return_value=manager,
    ):
        result = tool.execute(
            tasks=["a", "b"],
            mode="explore",
            run_in_background=True,
        )

    assert result == "Started 2 background sub-agent jobs: job-1, job-2"
    assert [call["task"] for call in manager.background_calls] == ["a", "b"]
    assert {call["mode"] for call in manager.background_calls} == {"explore"}
    assert manager.sync_calls == []


def test_agent_tool_rejects_batch_without_background_explore() -> None:
    tool = _tool()

    assert (
        tool.preflight_validate(
            tasks=["a", "b"],
            mode="explore",
            run_in_background=False,
        )
        == "Error: batch tasks require mode='explore' and run_in_background=true."
    )
    assert (
        tool.preflight_validate(
            tasks=["a", "b"],
            mode="verify",
            run_in_background=True,
        )
        == "Error: background sub-agent jobs require mode='explore'."
    )
