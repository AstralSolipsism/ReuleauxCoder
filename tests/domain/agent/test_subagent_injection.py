from types import SimpleNamespace

from reuleauxcoder.domain.agent.agent import Agent
from reuleauxcoder.domain.agent.events import AgentEventType


class _LLMStub:
    model = "stub-model"


def _make_agent() -> Agent:
    return Agent(llm=_LLMStub(), tools=[])


def _job(
    *,
    job_id: str = "sj_1",
    status: str = "completed",
    result: str | None = "done",
    error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        mode="explore",
        task="scan repo",
        status=status,
        result=result,
        error=error,
        injected_to_parent=False,
    )


def test_inject_subagent_job_result_appends_message_and_emits_events() -> None:
    agent = _make_agent()
    events = []
    agent.add_event_handler(events.append)

    job = _job()

    injected = agent.inject_subagent_job_result(job)

    assert injected is True
    assert job.injected_to_parent is True
    assert agent.state.messages[-1]["role"] == "assistant"
    assert "[Background sub-agent completed]" in agent.state.messages[-1]["content"]
    assert "done" in agent.state.messages[-1]["content"]
    assert [event.event_type for event in events] == [
        AgentEventType.SUBAGENT_COMPLETED,
        AgentEventType.TOOL_CALL_END,
    ]


def test_inject_subagent_job_result_is_idempotent() -> None:
    agent = _make_agent()

    job = _job()

    assert agent.inject_subagent_job_result(job) is True
    before = list(agent.state.messages)
    assert agent.inject_subagent_job_result(job) is False
    assert agent.state.messages == before


def test_inject_subagent_job_result_buffers_while_tool_call_pending() -> None:
    agent = _make_agent()
    events = []
    agent.add_event_handler(events.append)
    agent.state.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        }
    )

    job = _job()

    assert agent.inject_subagent_job_result(job) is True
    assert job.injected_to_parent is True
    assert len(agent.state.messages) == 1
    assert events == []

    agent.state.messages.append(
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    )

    assert agent._flush_pending_subagent_injections() == 1
    assert agent.state.messages[-1]["role"] == "assistant"
    assert "[Background sub-agent completed]" in agent.state.messages[-1]["content"]
    assert [event.event_type for event in events] == [
        AgentEventType.SUBAGENT_COMPLETED,
        AgentEventType.TOOL_CALL_END,
    ]


def test_flush_pending_subagent_injections_keeps_buffer_when_still_pending() -> None:
    agent = _make_agent()
    agent.state.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        }
    )

    assert agent.inject_subagent_job_result(_job()) is True
    assert agent._flush_pending_subagent_injections() == 0
    assert len(agent.state.messages) == 1


def test_flush_pending_subagent_injections_is_noop_when_empty() -> None:
    agent = _make_agent()

    assert agent._flush_pending_subagent_injections() == 0
    assert agent.state.messages == []
