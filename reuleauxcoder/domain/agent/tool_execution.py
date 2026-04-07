"""Tool execution - handles tool calls."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Callable
import concurrent.futures

if TYPE_CHECKING:
    from reuleauxcoder.domain.agent.agent import Agent
    from reuleauxcoder.domain.llm.models import ToolCall

from reuleauxcoder.domain.agent.events import AgentEvent


class ToolExecutor:
    """Handles tool execution for the agent."""

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def execute(self, tc: "ToolCall") -> str:
        """Execute a single tool call."""
        # First check agent's tools, then fall back to global registry
        tool = self.agent.get_tool(tc.name)
        if tool is None:
            from reuleauxcoder.extensions.tools.registry import get_tool

            tool = get_tool(tc.name)

        if tool is None:
            return f"Error: unknown tool '{tc.name}'"

        try:
            result = tool.execute(**tc.arguments)
            self.agent._emit_event(AgentEvent.tool_call_end(tc.name, result))
            return result
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        except Exception as e:
            self.agent._emit_event(AgentEvent.error(f"Error executing {tc.name}: {e}"))
            return f"Error executing {tc.name}: {e}"

    def execute_parallel(
        self,
        tool_calls: List["ToolCall"],
        on_tool: Optional[Callable[[str, dict], None]] = None,
    ) -> List[str]:
        """Execute multiple tool calls in parallel."""
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.execute, tc) for tc in tool_calls]
            return [f.result() for f in futures]
