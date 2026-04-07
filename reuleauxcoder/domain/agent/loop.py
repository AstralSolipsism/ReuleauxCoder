"""Agent loop - the main conversation loop."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Callable, List

if TYPE_CHECKING:
    from reuleauxcoder.domain.agent.agent import Agent

from reuleauxcoder.services.prompt.builder import system_prompt
from reuleauxcoder.domain.agent.events import AgentEvent


class AgentLoop:
    """Manages the agent's conversation loop."""

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _full_messages(self) -> list[dict]:
        """Get full messages including system prompt."""
        system = system_prompt(self.agent.tools)
        return [{"role": "system", "content": system}] + self.agent.state.messages

    def _tool_schemas(self) -> list[dict]:
        """Get tool schemas for LLM."""
        return [t.schema() for t in self.agent.tools]

    def run(
        self,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[str, dict], None]] = None,
    ) -> str:
        """Run the conversation loop."""
        # Compress if needed
        self.agent.context.maybe_compress(
            self.agent.state.messages,
            self.agent.llm,
        )

        for round_num in range(self.agent.max_rounds):
            self.agent.state.current_round = round_num

            # Call LLM
            resp = self.agent.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # Update token counts
            self.agent.state.total_prompt_tokens += resp.prompt_tokens
            self.agent.state.total_completion_tokens += resp.completion_tokens

            # No tool calls -> done
            if not resp.tool_calls:
                self.agent.state.messages.append(resp.message)
                return resp.content

            # Tool calls -> execute
            self.agent.state.messages.append(resp.message)

            if len(resp.tool_calls) == 1:
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self.agent._executor.execute(tc)
                self.agent.state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
            else:
                # Parallel execution
                results = self.agent._executor.execute_parallel(
                    resp.tool_calls, on_tool
                )
                for tc, result in zip(resp.tool_calls, results):
                    self.agent.state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )

            # Compress if tool outputs are big
            self.agent.context.maybe_compress(
                self.agent.state.messages,
                self.agent.llm,
            )

        return "(reached maximum tool-call rounds)"
