"""Tests for runner integration with remote execution."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib import request


_URLOPEN = request.build_opener(request.ProxyHandler({})).open

from reuleauxcoder.domain.agent.events import AgentEvent
from reuleauxcoder.domain.config.models import (
    Config,
    ContextConfig,
    MCPServerConfig,
    ModeConfig,
    RemoteExecConfig,
)
from reuleauxcoder.domain.approval import ApprovalRequest
from reuleauxcoder.domain.hooks.registry import HookRegistry
from reuleauxcoder.domain.llm.models import LLMResponse, ToolCall
from reuleauxcoder.extensions.remote_exec.backend import RemoteRelayToolBackend
from reuleauxcoder.extensions.remote_exec.http_service import RemoteRelayHTTPService
from reuleauxcoder.extensions.remote_exec.protocol import ToolPreviewResult
from reuleauxcoder.extensions.remote_exec.server import RelayServer
from reuleauxcoder.infrastructure.persistence.session_store import SessionStore
from reuleauxcoder.interfaces.entrypoint.runner import (
    AppDependencies,
    AppOptions,
    AppRunner,
)
from reuleauxcoder.interfaces.entrypoint.remote_relay import (
    _structured_ui_event_payload,
    _structured_ui_event_type,
)
from reuleauxcoder.interfaces.events import UIEvent, UIEventKind


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_request(
    method: str, url: str, payload: dict | None = None
) -> tuple[int, dict]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    with _URLOPEN(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else {}


class FakeLLM:
    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.debug_trace = False
        self.api_key = "key"
        self.base_url = None
        self.temperature = 0.0
        self.max_tokens = 2048
        self.ui_bus = None

    def reconfigure(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeContext:
    def __init__(self) -> None:
        self.max_tokens = 64000
        self._ui_bus = None

    def reconfigure(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens


class FakeAgent:
    def __init__(self, llm: FakeLLM, tools=None, chat_behavior=None) -> None:
        self.llm = llm
        self.tools = list(tools or [])
        self.context = FakeContext()
        self.state = SimpleNamespace(
            messages=[],
            total_prompt_tokens=0,
            total_completion_tokens=0,
            current_round=0,
        )
        self.messages = self.state.messages
        self.available_modes = {
            "coder": ModeConfig(name="coder", description="Default coding mode"),
            "debugger": ModeConfig(name="debugger", description="Debug mode"),
        }
        self.active_mode = "coder"
        self.active_main_model_profile = None
        self.active_sub_model_profile = None
        self.session_fingerprint = "local"
        self.hook_registry = HookRegistry()
        self._event_handlers = []
        self.approval_provider = None
        self._stop_requested = False
        self._chat_behavior = chat_behavior or (lambda _agent, prompt: f"ok:{prompt}")

    def register_hook(self, hook_point, hook) -> None:
        self.hook_registry.register(hook_point, hook)

    def add_event_handler(self, handler) -> None:
        self._event_handlers.append(handler)

    def set_mode(self, mode_name: str) -> None:
        self.active_mode = mode_name

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        response = self._chat_behavior(self, user_input)
        self.messages.append({"role": "assistant", "content": response})
        return response

    def request_stop(self) -> None:
        self._stop_requested = True

    def clear_stop_request(self) -> None:
        self._stop_requested = False

    def stop_requested(self) -> bool:
        return self._stop_requested


def _build_runner_with_fake_agent(
    relay_bind: str, chat_behavior=None, load_tools=None, session_dir: str | None = None
) -> AppRunner:
    config = Config(
        api_key="key",
        remote_exec=RemoteExecConfig(
            enabled=True, host_mode=True, relay_bind=relay_bind
        ),
        modes={
            "coder": ModeConfig(name="coder", description="Default coding mode"),
            "debugger": ModeConfig(name="debugger", description="Debug mode"),
        },
        active_mode="coder",
        session_dir=session_dir,
    )
    config.skills.enabled = False
    return AppRunner(
        options=AppOptions(),
        dependencies=AppDependencies(
            load_config=lambda _: config,
            create_llm=lambda cfg: FakeLLM(cfg.model),
            load_tools=load_tools or (lambda _backend: []),
            create_agent=lambda llm, _tools, _config: FakeAgent(
                llm, tools=_tools, chat_behavior=chat_behavior
            ),
        ),
    )


def _register_peer(
    base_url: str,
    bootstrap_token: str,
    cwd: str,
    capabilities: list[str] | None = None,
) -> tuple[str, str]:
    _, register_body = _json_request(
        "POST",
        f"{base_url}/remote/register",
        {
            "bootstrap_token": bootstrap_token,
            "cwd": cwd,
            "workspace_root": cwd,
            "capabilities": capabilities or [],
        },
    )
    payload = register_body["payload"]
    return payload["peer_id"], payload["peer_token"]


def _collect_stream_events(
    base_url: str, peer_token: str, chat_id: str, timeout_sec: float = 3.0
) -> list[dict]:
    deadline = time.time() + timeout_sec
    cursor = 0
    events: list[dict] = []
    while time.time() < deadline:
        _, stream_body = _json_request(
            "POST",
            f"{base_url}/remote/chat/stream",
            {
                "peer_token": peer_token,
                "chat_id": chat_id,
                "cursor": cursor,
                "timeout_sec": 0.5,
            },
        )
        events.extend(stream_body["events"])
        cursor = stream_body["next_cursor"]
        if stream_body["done"]:
            return events
    raise AssertionError("timed out waiting for stream events")


def test_remote_relay_maps_all_ui_event_kinds_to_structured_events() -> None:
    expected = {
        UIEventKind.VIEW: "view",
        UIEventKind.CONTEXT: "context_event",
        UIEventKind.REMOTE: "remote_event",
        UIEventKind.MCP: "mcp_event",
        UIEventKind.MODEL: "model_event",
        UIEventKind.SESSION: "session_event",
        UIEventKind.COMMAND: "command_event",
        UIEventKind.APPROVAL: "approval_event",
        UIEventKind.SYSTEM: "system_event",
        UIEventKind.AGENT: "agent_event",
    }
    for kind, event_type in expected.items():
        event = UIEvent.info("hello", kind=kind, detail="value")
        assert _structured_ui_event_type(event) == event_type
        payload = _structured_ui_event_payload(event)
        assert payload["message"] == "hello"
        assert payload["kind"] == kind.value
        assert payload["detail"] == "value"


class TestRunnerRemoteExec:
    def test_local_mode_no_relay(self, tmp_path: Path) -> None:
        """When remote_exec is disabled, runner starts normally with local backend."""
        config = Config(remote_exec=RemoteExecConfig(enabled=False))
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        assert runner._relay_server is None
        assert ctx.agent is not None
        runner.cleanup(ctx.agent)

    def test_local_mode_smoke_startup_uses_local_backends(self, tmp_path: Path) -> None:
        """Smoke test: normal local startup should not initialize remote services."""
        config = Config(
            api_key="key",
            remote_exec=RemoteExecConfig(enabled=False, host_mode=False),
        )
        runner = AppRunner(
            options=AppOptions(server_mode=False),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is None
            assert runner._relay_http_service is None
            assert ctx.agent is not None
            assert len(ctx.agent.tools) > 0
            assert all(
                getattr(tool.backend, "backend_id", None) == "local"
                for tool in ctx.agent.tools
            )
        finally:
            runner.cleanup(ctx.agent)

    def test_remote_enabled_host_mode_starts_relay(self, tmp_path: Path) -> None:
        config = Config(
            remote_exec=RemoteExecConfig(
                enabled=True,
                host_mode=True,
                relay_bind=f"127.0.0.1:{_free_port()}",
            )
        )
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        assert runner._relay_server is not None
        assert isinstance(runner._relay_server, RelayServer)
        assert all(
            isinstance(tool.backend, RemoteRelayToolBackend) for tool in ctx.agent.tools
        )
        runner.cleanup(ctx.agent)
        assert runner._relay_server is None

    def test_remote_relay_uses_configured_peer_token_ttl(
        self, tmp_path: Path
    ) -> None:
        config = Config(
            remote_exec=RemoteExecConfig(
                enabled=True,
                host_mode=True,
                relay_bind=f"127.0.0.1:{_free_port()}",
                peer_token_ttl_sec=123,
            )
        )
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_server._peer_token_ttl_sec == 123
        finally:
            runner.cleanup(ctx.agent)

    def test_remote_init_failure_does_not_crash(self, tmp_path: Path) -> None:
        def bad_relay_factory(_config: Config) -> RelayServer:
            raise RuntimeError("boom")

        config = Config(remote_exec=RemoteExecConfig(enabled=True, host_mode=True))
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
                create_remote_relay_server=bad_relay_factory,
            ),
        )
        ctx = runner.initialize()
        assert runner._relay_server is None
        assert ctx.agent is not None
        runner.cleanup(ctx.agent)

    def test_cleanup_runs_relay_cleanup(self, tmp_path: Path) -> None:
        config = Config(
            remote_exec=RemoteExecConfig(
                enabled=True,
                host_mode=True,
                relay_bind=f"127.0.0.1:{_free_port()}",
            )
        )
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        assert runner._relay_server is not None
        # no peers connected, cleanup should still complete without error
        runner.cleanup(ctx.agent)
        assert runner._relay_server is None

    def test_runner_preserves_context_config_on_agent(self, tmp_path: Path) -> None:
        config = Config(
            api_key="key",
            context=ContextConfig(
                snip_keep_recent_tools=9,
                snip_threshold_chars=3210,
                snip_min_lines=8,
                summarize_keep_recent_turns=6,
            ),
            remote_exec=RemoteExecConfig(enabled=False),
        )
        runner = AppRunner(
            options=AppOptions(),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        assert (
            getattr(ctx.agent, "config", None) is None
            or getattr(ctx.agent, "config", None) == config
        )
        assert ctx.agent.max_context_tokens == config.max_context_tokens
        runner.cleanup(ctx.agent)

    def test_attach_mcp_starts_server_and_both_placements(self, monkeypatch) -> None:
        config = Config(
            mcp_servers=[
                MCPServerConfig(name="server-only", command="a", placement="server"),
                MCPServerConfig(name="peer-only", command="b", placement="peer"),
                MCPServerConfig(name="shared", command="c", placement="both"),
            ]
        )
        runner = AppRunner(options=AppOptions())
        agent = SimpleNamespace()
        started: list[str] = []

        def fake_init_mcp(servers, _agent, _ui_bus):
            started.extend(server.name for server in servers)
            return "manager"

        monkeypatch.setattr(runner, "_init_mcp", fake_init_mcp)

        manager = runner._attach_mcp_if_configured(config, agent, None)

        assert manager == "manager"
        assert agent.mcp_manager == "manager"
        assert started == ["server-only", "shared"]

    def test_server_mode_smoke_bootstrap_endpoint(self, tmp_path: Path) -> None:
        relay_bind = "127.0.0.1:18765"
        bootstrap_secret = "runner-secret"
        config = Config(
            api_key="key",
            remote_exec=RemoteExecConfig(
                enabled=True,
                host_mode=True,
                relay_bind=relay_bind,
                bootstrap_access_secret=bootstrap_secret,
            ),
        )
        runner = AppRunner(
            options=AppOptions(server_mode=True),
            dependencies=AppDependencies(
                load_config=lambda _: config,
            ),
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            assert isinstance(runner._relay_http_service, RemoteRelayHTTPService)

            req = request.Request(
                f"http://{relay_bind}/remote/bootstrap.sh",
                headers={"X-RC-Bootstrap-Secret": bootstrap_secret},
                method="GET",
            )
            with _URLOPEN(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                content_type = resp.headers.get_content_type()

            assert resp.status == 200
            assert content_type in {"text/x-shellscript", "text/plain"}
            assert "#!/bin/sh" in body
            assert "RC_HOST" in body
            assert "rcoder-peer" in body
            assert "/remote/artifacts/{os}/{arch}/rcoder-peer" in body
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_emits_startup_event(self) -> None:
        workspace = Path(__file__).resolve().parent
        port = _free_port()
        runner = _build_runner_with_fake_agent(f"127.0.0.1:{port}")
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            peer_id, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace),
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "hello"},
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url, peer_token, start_body["chat_id"]
            )
            ready_events = [
                event for event in events if event["type"] == "remote_peer_ready"
            ]
            assert ready_events
            payload = ready_events[0]["payload"]
            assert payload["peer_id"] == peer_id
            assert payload["session_id"]
            assert payload["fingerprint"]
            assert payload["mode"] == "coder"
            assert payload["model"]
            assert not any(
                event["type"] == "output"
                and "REMOTE PEER READY" in event["payload"].get("content", "")
                for event in events
            )
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_uses_explicit_session_hint(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        port = _free_port()

        def chat_behavior(agent: FakeAgent, prompt: str) -> str:
            existing = [
                message.get("content")
                for message in agent.messages
                if message.get("role") == "user"
            ]
            return f"{prompt}|history={','.join(existing)}|sid={getattr(agent, 'current_session_id', '-')}"

        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}",
            chat_behavior=chat_behavior,
            session_dir=str(tmp_path / "sessions"),
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            peer_id, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace),
            )
            store = SessionStore(tmp_path / "sessions")
            fingerprint = f"remote:{peer_id}:{workspace}"
            store.save(
                [{"role": "user", "content": "old-context"}],
                "fake-model",
                "session-old",
                fingerprint=fingerprint,
            )

            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {
                    "peer_token": peer_token,
                    "prompt": "fresh",
                    "session_hint": "session-new",
                },
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url, peer_token, start_body["chat_id"]
            )
            ready = [
                event["payload"]
                for event in events
                if event["type"] == "remote_peer_ready"
            ][0]
            end = [event for event in events if event["type"] == "chat_end"][-1]
            assert ready["session_id"] == "session-new"
            assert "old-context" not in end["payload"]["response"]
            assert "fresh|history=fresh|sid=session-new" in end["payload"]["response"]
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_remote_session_endpoints_roundtrip(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        port = _free_port()
        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}", session_dir=str(tmp_path / "sessions")
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            _, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace),
            )
            _, created = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/sessions/new",
                {"peer_token": peer_token},
            )
            session_id = created["metadata"]["id"]
            assert created["ok"] is True
            assert session_id

            _, listed = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/sessions/list",
                {"peer_token": peer_token},
            )
            assert any(item["id"] == session_id for item in listed["sessions"])

            snapshot = {
                "version": 1,
                "sessionId": session_id,
                "turns": [{"userMessage": {"text": "hello"}, "assistantMessages": []}],
            }
            _, saved = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/sessions/snapshot",
                {
                    "peer_token": peer_token,
                    "session_id": session_id,
                    "snapshot": snapshot,
                },
            )
            assert saved["ok"] is True

            _, loaded = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/sessions/load",
                {"peer_token": peer_token, "session_id": session_id},
            )
            assert loaded["metadata"]["id"] == session_id
            assert loaded["snapshot"]["turns"][0]["userMessage"]["text"] == "hello"
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_uses_structured_tool_events(self) -> None:
        workspace = Path(__file__).resolve().parent
        long_result = "file body\n" + ("x" * 700)

        def emit(agent: FakeAgent, event: AgentEvent) -> None:
            for handler in list(agent._event_handlers):
                handler(event)

        def chat_behavior(agent: FakeAgent, _prompt: str) -> str:
            emit(agent, AgentEvent.stream_token("Before tool"))
            emit(
                agent,
                AgentEvent.tool_call_start(
                    "read_file", {"file_path": str(workspace / "decision.md")}
                ),
            )
            emit(
                agent,
                AgentEvent.tool_call_end(
                    "read_file", long_result, success=True
                ),
            )
            return "done"

        port = _free_port()
        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}", chat_behavior=chat_behavior
        )
        ctx = runner.initialize()
        try:
            _, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace),
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "read"},
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url, peer_token, start_body["chat_id"]
            )
            assistant_text = "".join(
                event["payload"].get("content", "")
                for event in events
                if event["type"] == "assistant_delta"
            )
            terminal_text = "\n".join(
                event["payload"].get("content", "")
                for event in events
                if event["type"] == "output"
            )

            assert assistant_text == "Before tool"
            assert "TOOL CALL" not in terminal_text
            assert "read_file(" not in terminal_text
            assert "file body" not in terminal_text
            assert any(
                event["type"] == "tool_call_start"
                and event["payload"].get("tool_name") == "read_file"
                for event in events
            )
            assert any(
                event["type"] == "tool_call_end"
                and event["payload"].get("tool_name") == "read_file"
                and event["payload"].get("tool_result") == long_result
                and len(event["payload"].get("tool_result", "")) > 500
                for event in events
            )
            assert any(
                event["type"] == "chat_end"
                and event["payload"].get("response_rendered") is True
                for event in events
            )
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_remote_approval_uses_peer_preview(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        preview_calls: list[dict] = []

        def fake_preview(self, peer_id, request, timeout_sec=None):
            preview_calls.append(
                {"peer_id": peer_id, "tool_name": request.tool_name, "args": request.args}
            )
            return ToolPreviewResult(
                ok=True,
                sections=[
                    {
                        "id": "diff",
                        "title": "Proposed file diff",
                        "kind": "diff",
                        "content": "--- a/demo.txt\n+++ b/demo.txt\n-peer\n+host\n",
                        "resolved_path": str(tmp_path / "demo.txt"),
                        "original_text": "peer\n",
                        "modified_text": "host\n",
                    }
                ],
                resolved_path=str(tmp_path / "demo.txt"),
                old_sha256="peer-sha",
                old_exists=True,
            )

        monkeypatch.setattr(RelayServer, "send_preview_request", fake_preview)

        def chat_behavior(agent: FakeAgent, _prompt: str) -> str:
            decision = agent.approval_provider.request_approval(
                ApprovalRequest(
                    tool_name="write_file",
                    tool_args={"file_path": "demo.txt", "content": "host\n"},
                    tool_source="builtin",
                    reason="confirm write",
                )
            )
            assert decision.approved
            return "approved"

        port = _free_port()
        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}",
            chat_behavior=chat_behavior,
            load_tools=lambda backend: [SimpleNamespace(name="write_file", backend=backend)],
        )
        ctx = runner.initialize()
        try:
            peer_id, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(tmp_path),
                capabilities=["tool_preview"],
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "write"},
            )
            cursor = 0
            approval_events: list[dict] = []
            deadline = time.time() + 3
            while time.time() < deadline and not approval_events:
                _, stream_body = _json_request(
                    "POST",
                    f"{runner._relay_http_service.base_url}/remote/chat/stream",
                    {
                        "peer_token": peer_token,
                        "chat_id": start_body["chat_id"],
                        "cursor": cursor,
                        "timeout_sec": 0.5,
                    },
                )
                cursor = stream_body["next_cursor"]
                approval_events = [
                    event
                    for event in stream_body["events"]
                    if event["type"] == "approval_request"
                ]
            assert approval_events
            payload = approval_events[0]["payload"]
            assert preview_calls[0]["peer_id"] == peer_id
            assert payload["preview_unavailable"] is False
            assert payload["sections"][0]["kind"] == "diff"
            assert payload["sections"][0]["original_text"] == "peer\n"
            assert "confirm write" in payload["content"]

            _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/approval/reply",
                {
                    "peer_token": peer_token,
                    "chat_id": start_body["chat_id"],
                    "approval_id": payload["approval_id"],
                    "decision": "allow_once",
                },
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url,
                peer_token,
                start_body["chat_id"],
            )
            assert any(
                event["type"] == "chat_end"
                and event["payload"].get("response") == "approved"
                for event in events
            )
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_chat_cancel_denies_pending_approval(self, tmp_path: Path) -> None:
        decisions: list[tuple[bool, str | None]] = []

        def chat_behavior(agent: FakeAgent, _prompt: str) -> str:
            decision = agent.approval_provider.request_approval(
                ApprovalRequest(
                    tool_name="shell",
                    tool_args={"command": "gitnexus --version"},
                    tool_source="builtin_tool",
                    reason="Tool 'shell' requires approval by policy",
                )
            )
            decisions.append((decision.approved, decision.reason))
            return "cancelled" if not decision.approved else "approved"

        port = _free_port()
        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}", chat_behavior=chat_behavior
        )
        ctx = runner.initialize()
        try:
            _, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(tmp_path),
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "run shell"},
            )

            cursor = 0
            approval_payload: dict | None = None
            deadline = time.time() + 3
            while time.time() < deadline and approval_payload is None:
                _, stream_body = _json_request(
                    "POST",
                    f"{runner._relay_http_service.base_url}/remote/chat/stream",
                    {
                        "peer_token": peer_token,
                        "chat_id": start_body["chat_id"],
                        "cursor": cursor,
                        "timeout_sec": 0.5,
                    },
                )
                cursor = stream_body["next_cursor"]
                for event in stream_body["events"]:
                    if event["type"] == "approval_request":
                        approval_payload = event["payload"]
                        break
            assert approval_payload is not None

            _, cancelled = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/cancel",
                {
                    "peer_token": peer_token,
                    "chat_id": start_body["chat_id"],
                    "reason": "user_cancelled",
                },
            )
            assert cancelled["ok"] is True

            events = _collect_stream_events(
                runner._relay_http_service.base_url,
                peer_token,
                start_body["chat_id"],
            )
            assert decisions == [(False, "user_cancelled")]
            assert any(event["type"] == "chat_cancel_requested" for event in events)
            assert any(
                event["type"] == "approval_resolved"
                and event["payload"].get("approval_id")
                == approval_payload["approval_id"]
                and event["payload"].get("decision") == "deny_once"
                and event["payload"].get("reason") == "user_cancelled"
                for event in events
            )
            assert any(event["type"] == "chat_cancelled" for event in events)
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_keeps_peer_sessions_isolated(self) -> None:
        workspace = Path(__file__).resolve().parent
        port = _free_port()

        def chat_behavior(agent: FakeAgent, prompt: str) -> str:
            time.sleep(0.15)
            return f"reply:{prompt}:{getattr(agent, 'current_session_id', '-')}"

        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}", chat_behavior=chat_behavior
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            peer_a, token_a = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace / "peer-a"),
            )
            peer_b, token_b = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace / "peer-b"),
            )

            starts: dict[str, dict] = {}

            def start_chat(label: str, token: str) -> None:
                _, body = _json_request(
                    "POST",
                    f"{runner._relay_http_service.base_url}/remote/chat/start",
                    {"peer_token": token, "prompt": label},
                )
                starts[label] = body

            t1 = threading.Thread(target=start_chat, args=("alpha", token_a))
            t2 = threading.Thread(target=start_chat, args=("beta", token_b))
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)

            events_a = _collect_stream_events(
                runner._relay_http_service.base_url, token_a, starts["alpha"]["chat_id"]
            )
            events_b = _collect_stream_events(
                runner._relay_http_service.base_url, token_b, starts["beta"]["chat_id"]
            )

            ready_a = [
                event["payload"]
                for event in events_a
                if event["type"] == "remote_peer_ready"
            ][0]
            ready_b = [
                event["payload"]
                for event in events_b
                if event["type"] == "remote_peer_ready"
            ][0]
            end_a = [event for event in events_a if event["type"] == "chat_end"][-1]
            end_b = [event for event in events_b if event["type"] == "chat_end"][-1]

            assert ready_a["peer_id"] == peer_a
            assert ready_b["peer_id"] == peer_b
            assert peer_b not in ready_a["fingerprint"]
            assert peer_a not in ready_b["fingerprint"]
            assert end_a["payload"]["response"].startswith("reply:alpha:")
            assert end_b["payload"]["response"].startswith("reply:beta:")
            assert end_a["payload"]["response"] != end_b["payload"]["response"]
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_sets_remote_runtime_working_directory(
        self, tmp_path: Path
    ) -> None:
        port = _free_port()

        def chat_behavior(agent: FakeAgent, _prompt: str) -> str:
            return f"cwd:{getattr(agent, 'runtime_working_directory', '<missing>')}"

        runner = _build_runner_with_fake_agent(
            f"127.0.0.1:{port}", chat_behavior=chat_behavior
        )
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            _, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(tmp_path),
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "hello"},
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url, peer_token, start_body["chat_id"]
            )
            end_event = [event for event in events if event["type"] == "chat_end"][-1]
            assert end_event["payload"]["response"] == f"cwd:{tmp_path}"
        finally:
            runner.cleanup(ctx.agent)

    def test_runner_stream_chat_slash_command_renders_structured_view(self) -> None:
        workspace = Path(__file__).resolve().parent
        port = _free_port()
        runner = _build_runner_with_fake_agent(f"127.0.0.1:{port}")
        ctx = runner.initialize()
        try:
            assert runner._relay_server is not None
            assert runner._relay_http_service is not None
            _, peer_token = _register_peer(
                runner._relay_http_service.base_url,
                runner._relay_server.issue_bootstrap_token(ttl_sec=60),
                str(workspace),
            )
            _, start_body = _json_request(
                "POST",
                f"{runner._relay_http_service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "/help"},
            )
            events = _collect_stream_events(
                runner._relay_http_service.base_url, peer_token, start_body["chat_id"]
            )
            view_events = [event for event in events if event["type"] == "view"]
            view_payloads = "\n".join(
                json.dumps(event["payload"], ensure_ascii=False)
                for event in view_events
            )
            terminal_outputs = [
                event["payload"]["content"]
                for event in events
                if event["type"] == "output"
                and event["payload"].get("format") == "terminal"
            ]
            merged = "\n".join(terminal_outputs)
            assert any(event["type"] == "remote_peer_ready" for event in events)
            assert "REMOTE PEER READY" not in merged
            assert view_events
            assert "Available commands" in view_payloads or "/help" in view_payloads
            assert not any(
                event["type"] == "output"
                and event["payload"].get("format") == "plain"
                and "Open view:" in event["payload"].get("content", "")
                for event in events
            )
        finally:
            runner.cleanup(ctx.agent)
