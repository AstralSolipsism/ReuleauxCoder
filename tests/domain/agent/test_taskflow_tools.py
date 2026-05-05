from __future__ import annotations

from types import SimpleNamespace

from reuleauxcoder.domain.agent.agent import Agent
from reuleauxcoder.domain.config.models import ModeConfig
from reuleauxcoder.domain.taskflow.models import GoalStatus
from reuleauxcoder.extensions.tools.taskflow import TaskflowPlanningTool
from ezcode_server.services.taskflow.service import TaskflowService


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name}}


def test_taskflow_tool_remains_visible_inside_restricted_session_mode() -> None:
    agent = Agent(
        llm=SimpleNamespace(),
        tools=[_Tool("read_file"), _Tool("taskflow_update"), _Tool("write_file")],
        available_modes={
            "planner": ModeConfig(
                name="planner",
                tools=["read_file"],
                prompt_append="Plan first.",
            )
        },
        active_mode="planner",
    )

    assert [tool.name for tool in agent.get_active_tools()] == ["read_file"]

    agent.workflow_mode = "taskflow"

    assert [tool.name for tool in agent.get_active_tools()] == [
        "read_file",
        "taskflow_update",
    ]


def test_taskflow_planning_tool_cannot_confirm_goal() -> None:
    service = TaskflowService()
    goal = service.create_goal(title="Plan", prompt="plan")
    tool = TaskflowPlanningTool(service, goal_id=goal.id)

    result = tool.execute("confirm_goal")

    assert "unknown Taskflow operation" in result
    assert service.get_goal(goal.id).status == GoalStatus.PLANNING
    operations = tool.parameters["properties"]["operation"]["enum"]
    assert "confirm_goal" not in operations
