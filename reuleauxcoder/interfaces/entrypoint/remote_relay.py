"""Remote relay bootstrap and peer chat binding helpers."""

from __future__ import annotations

import json
import inspect
from pathlib import Path
import uuid
from typing import Any, Callable

from rich.console import Console

from reuleauxcoder.app.runtime.session_state import (
    apply_session_runtime_state,
    build_session_runtime_state,
    restore_config_runtime_defaults,
)
from reuleauxcoder.app.runtime.agent_runtime import (
    AgentRuntimeCancelled,
    get_agent_runtime_limiter,
)
from reuleauxcoder.domain.agent.agent import Agent
from reuleauxcoder.domain.agent.events import AgentEvent, AgentEventType
from reuleauxcoder.domain.approval import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
    PendingApproval,
)
from reuleauxcoder.domain.config.models import Config
from reuleauxcoder.domain.session.models import Session, SessionMetadata, SessionRuntimeState
from reuleauxcoder.extensions.remote_exec.backend import RemoteRelayToolBackend
from reuleauxcoder.extensions.remote_exec.mcp_tools import RemotePeerMCPTool
from reuleauxcoder.extensions.remote_exec.protocol import ChatResponse, ToolPreviewResult
from reuleauxcoder.extensions.remote_exec.server import RelayServer
from reuleauxcoder.extensions.skills.service import SkillsService
from reuleauxcoder.extensions.tools.backend import ExecutionContext
from reuleauxcoder.interfaces.cli.commands import handle_command
from reuleauxcoder.interfaces.cli.registration import CLI_PROFILE
from reuleauxcoder.interfaces.cli.render import CLIRenderer
from reuleauxcoder.interfaces.entrypoint.dependencies import AppDependencies
from reuleauxcoder.interfaces.events import UIEventBus, UIEventKind


def init_remote_relay(runner, config: Config, ui_bus: UIEventBus) -> None:
    """Initialize remote relay server if enabled and host_mode."""
    try:
        relay = runner.dependencies.create_remote_relay_server(config)
    except Exception as exc:
        ui_bus.warning(
            f"Remote relay initialization failed: {exc}", kind=UIEventKind.REMOTE
        )
        return
    if relay is None:
        return
    try:
        relay.start()
        runner._relay_server = relay
    except Exception as exc:
        ui_bus.warning(
            f"Remote relay server failed to start: {exc}", kind=UIEventKind.REMOTE
        )
        return

    try:
        http_service = runner.dependencies.create_remote_http_service(
            config, relay, ui_bus
        )
    except Exception as exc:
        relay.stop()
        runner._relay_server = None
        ui_bus.warning(
            f"Remote relay HTTP service initialization failed: {exc}",
            kind=UIEventKind.REMOTE,
        )
        return

    if http_service is not None:
        try:
            http_service.start()
            runner._relay_http_service = http_service
        except Exception as exc:
            relay.stop()
            runner._relay_server = None
            runner._relay_http_service = None
            ui_bus.warning(
                f"Remote relay HTTP service failed to start: {exc}",
                kind=UIEventKind.REMOTE,
            )
            return

    ui_bus.success(
        "Remote relay server started.",
        kind=UIEventKind.REMOTE,
        bind=getattr(config.remote_exec, "relay_bind", None),
        base_url=runner._relay_http_service.base_url
        if runner._relay_http_service
        else None,
    )


def bind_remote_chat_handler(runner, agent: Agent) -> None:
    """Bind remote chat handlers for interactive peers."""
    if runner._relay_http_service is None or runner._relay_server is None:
        return

    relay_server: RelayServer = runner._relay_server
    config = getattr(agent, "runtime_config", None)
    runtime_config: dict[str, Config | None] = {"value": config}
    ui_bus = getattr(agent.context, "_ui_bus", None)
    sessions_dir = (
        Path(config.session_dir)
        if config and getattr(config, "session_dir", None)
        else None
    )
    skills_service: SkillsService | None = getattr(agent, "skills_service", None)
    session_store = runner.dependencies.create_session_store(sessions_dir)
    startup_announced: set[tuple[str, str, str]] = set()
    agent_runtime_limiter = get_agent_runtime_limiter()
    if config is not None:
        agent_runtime_limiter.configure(
            max_running_agents=config.agent_runtime.max_running_agents,
            max_shells_per_agent=config.agent_runtime.max_shells_per_agent,
        )

    def _current_config() -> Config | None:
        return runtime_config["value"]

    def _reload_config() -> None:
        next_config = runner.dependencies.load_config(runner.options.config_path)
        setattr(next_config, "_source_path", runner.options.config_path)
        if runner.options.server_mode:
            next_config.remote_exec.enabled = True
            next_config.remote_exec.host_mode = True
        errors = next_config.validate()
        if errors:
            raise ValueError("; ".join(errors))
        runtime_config["value"] = next_config
        agent_runtime_limiter.configure(
            max_running_agents=next_config.agent_runtime.max_running_agents,
            max_shells_per_agent=next_config.agent_runtime.max_shells_per_agent,
        )
        runner._relay_http_service.mcp_servers = list(next_config.mcp_servers)
        runner._relay_http_service.mcp_artifact_root = Path(
            next_config.mcp_artifact_root
        )
        runner._relay_http_service.environment_cli_tools = dict(
            next_config.environment.cli_tools
        )
        runner._relay_http_service.environment_skills = dict(
            next_config.environment.skills
        )
        runner._relay_http_service.bootstrap_access_secret = (
            next_config.remote_exec.bootstrap_access_secret
        )
        runner._relay_http_service.admin_access_secret = (
            next_config.remote_exec.admin_access_secret
        )
        if ui_bus is not None:
            ui_bus.info("Remote admin config reloaded.", kind=UIEventKind.REMOTE)

    runner._relay_http_service.admin_manager.reload_handler = _reload_config

    def _peer_fingerprint(peer_id: str) -> str:
        peer = relay_server.registry.get(peer_id)
        workspace_root = peer.workspace_root if peer is not None else "."
        machine_key = peer_id
        if peer is not None:
            host_info = (
                peer.meta.get("host_info_min") if isinstance(peer.meta, dict) else None
            )
            if isinstance(host_info, dict):
                machine_key = str(
                    host_info.get("hostname") or host_info.get("machine_id") or peer_id
                )
        return f"remote:{machine_key}:{workspace_root or '.'}"

    def _session_snapshot_path(session_id: str) -> Path:
        session_path = session_store._get_session_path(session_id)
        return session_path.with_name(f"{session_path.stem}.ui.json")

    def _session_metadata_payload(
        session: Session | SessionMetadata,
    ) -> dict[str, Any]:
        preview = (
            session.preview
            if isinstance(session, SessionMetadata)
            else session.get_preview()
        )
        return {
            "id": session.id,
            "model": session.model,
            "saved_at": session.saved_at,
            "preview": preview,
            "fingerprint": session.fingerprint,
        }

    def _load_session_snapshot(session_id: str) -> tuple[dict[str, Any] | None, str | None]:
        path = _session_snapshot_path(session_id)
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, str(exc)
        if not isinstance(data, dict):
            return None, "snapshot_not_object"
        return data, None

    def _handle_session_request(
        action: str, peer_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        fingerprint = _peer_fingerprint(peer_id)
        current_config = _current_config()
        if action == "list":
            limit = max(1, min(100, int(payload.get("limit", 20) or 20)))
            sessions = session_store.list(limit=limit, fingerprint=fingerprint)
            return {
                "ok": True,
                "fingerprint": fingerprint,
                "sessions": [_session_metadata_payload(session) for session in sessions],
            }

        if action == "load":
            session_id = str(payload.get("session_id") or "")
            if not session_id:
                return {"ok": False, "error": "missing_session_id", "_status": 400}
            loaded = session_store.load(session_id)
            if loaded is None:
                return {"ok": False, "error": "session_not_found", "_status": 404}
            if loaded.fingerprint != fingerprint:
                return {
                    "ok": False,
                    "error": "session_fingerprint_mismatch",
                    "fingerprint": loaded.fingerprint,
                    "current_fingerprint": fingerprint,
                    "_status": 403,
                }
            snapshot, snapshot_error = _load_session_snapshot(session_id)
            return {
                "ok": True,
                "fingerprint": fingerprint,
                "metadata": _session_metadata_payload(loaded),
                "messages": list(loaded.messages),
                "runtime_state": loaded.runtime_state.to_dict(),
                "snapshot": snapshot,
                "snapshot_error": snapshot_error,
            }

        if action == "new":
            if current_config is None:
                return {"ok": False, "error": "config_unavailable", "_status": 503}
            session_id = session_store.generate_session_id()
            runtime_state = SessionRuntimeState(
                model=getattr(current_config, "model", None),
                active_mode=getattr(current_config, "active_mode", None),
                active_main_model_profile=getattr(
                    current_config, "active_main_model_profile", None
                )
                or getattr(current_config, "active_model_profile", None),
                active_sub_model_profile=getattr(
                    current_config, "active_sub_model_profile", None
                ),
            )
            return {
                "ok": True,
                "fingerprint": fingerprint,
                "metadata": {
                    "id": session_id,
                    "model": getattr(current_config, "model", ""),
                    "saved_at": "",
                    "preview": "",
                    "fingerprint": fingerprint,
                },
                "messages": [],
                "runtime_state": runtime_state.to_dict(),
                "snapshot": None,
            }

        if action == "delete":
            session_id = str(payload.get("session_id") or "")
            if not session_id:
                return {"ok": False, "error": "missing_session_id", "_status": 400}
            loaded = session_store.load(session_id)
            if loaded is None:
                return {"ok": False, "error": "session_not_found", "_status": 404}
            if loaded.fingerprint != fingerprint:
                return {
                    "ok": False,
                    "error": "session_fingerprint_mismatch",
                    "fingerprint": loaded.fingerprint,
                    "current_fingerprint": fingerprint,
                    "_status": 403,
                }
            deleted = session_store.delete(session_id)
            snapshot_path = _session_snapshot_path(session_id)
            if snapshot_path.exists():
                snapshot_path.unlink()
            return {
                "ok": deleted,
                "session_id": session_id,
                "fingerprint": fingerprint,
            }

        if action == "snapshot":
            session_id = str(payload.get("session_id") or "")
            snapshot = payload.get("snapshot")
            if not session_id:
                return {"ok": False, "error": "missing_session_id", "_status": 400}
            if not isinstance(snapshot, dict):
                return {"ok": False, "error": "invalid_snapshot", "_status": 400}
            loaded = session_store.load(session_id)
            if loaded is None:
                return {"ok": False, "error": "session_not_found", "_status": 404}
            if loaded.fingerprint != fingerprint:
                return {
                    "ok": False,
                    "error": "session_fingerprint_mismatch",
                    "_status": 403,
                }
            path = _session_snapshot_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {"ok": True, "session_id": session_id}

        return {"ok": False, "error": "unknown_session_action", "_status": 404}

    runner._relay_http_service.set_session_handler(_handle_session_request)

    def _create_peer_agent(
        peer_id: str,
        remote_stream_handler: Callable[..., None] | None = None,
        session_hint: str | None = None,
        resume_latest: bool = True,
    ) -> Agent:
        current_config = _current_config()
        if current_config is None:
            return agent

        peer_llm = runner.dependencies.create_llm(current_config)
        peer_llm.ui_bus = ui_bus
        peer_backend = RemoteRelayToolBackend(relay_server=relay_server, ui_bus=ui_bus)
        peer_tools = runner.dependencies.load_tools(peer_backend)
        peer_agent = runner.dependencies.create_agent(
            peer_llm, peer_tools, current_config
        )
        server_mcp_manager = getattr(agent, "mcp_manager", None)
        server_mcp_tools = list(getattr(server_mcp_manager, "tools", []) or [])
        if server_mcp_tools:
            peer_agent.add_tools(server_mcp_tools)
        setattr(peer_agent, "runtime_config", current_config)
        setattr(peer_agent, "skills_service", skills_service)
        setattr(peer_agent, "skills_catalog", getattr(agent, "skills_catalog", ""))
        runner._register_hooks(peer_agent, current_config)
        runner._wire_agent_tool_parent(peer_agent)

        peer = relay_server.registry.get(peer_id)
        workspace_root = peer.workspace_root if peer is not None else None
        runtime_cwd = workspace_root or (peer.cwd if peer is not None else None)
        if runtime_cwd:
            setattr(peer_agent, "runtime_working_directory", runtime_cwd)
        for tool_info in relay_server.get_peer_mcp_tools(peer_id):
            peer_agent.add_tools([RemotePeerMCPTool(peer_backend, tool_info)])
        for tool in peer_agent.tools:
            backend = getattr(tool, "backend", None)
            if getattr(backend, "backend_id", None) != "remote_relay":
                continue
            context = getattr(backend, "context", None)
            if not isinstance(context, ExecutionContext):
                continue
            context.peer_id = peer_id
            context.remote_stream_handler = remote_stream_handler
            if workspace_root:
                context.workspace_root = workspace_root

        fingerprint = _peer_fingerprint(peer_id)
        setattr(peer_agent, "session_fingerprint", fingerprint)

        if session_hint:
            loaded = session_store.load(session_hint)
            if loaded is not None:
                if loaded.fingerprint != fingerprint:
                    raise ValueError(
                        f"Session '{session_hint}' belongs to fingerprint "
                        f"'{loaded.fingerprint}', current fingerprint is '{fingerprint}'."
                    )
                apply_session_runtime_state(loaded, current_config, peer_agent)
            else:
                restore_config_runtime_defaults(current_config, peer_agent)
            setattr(peer_agent, "current_session_id", session_hint)
            return peer_agent

        if resume_latest:
            latest = session_store.get_latest(fingerprint=fingerprint)
            if latest:
                loaded = session_store.load(latest.id)
                if loaded is not None:
                    apply_session_runtime_state(loaded, current_config, peer_agent)
                    setattr(peer_agent, "current_session_id", latest.id)
                    return peer_agent

        restore_config_runtime_defaults(current_config, peer_agent)
        setattr(peer_agent, "current_session_id", session_store.generate_session_id())
        return peer_agent

    def _save_peer_session(peer_agent: Agent, peer_id: str) -> None:
        current_config = _current_config()
        if current_config is None or not getattr(peer_agent, "messages", None):
            return
        sid = session_store.save(
            peer_agent.messages,
            getattr(peer_agent.llm, "model", current_config.model),
            getattr(peer_agent, "current_session_id", None),
            total_prompt_tokens=peer_agent.state.total_prompt_tokens,
            total_completion_tokens=peer_agent.state.total_completion_tokens,
            active_mode=getattr(peer_agent, "active_mode", None),
            runtime_state=build_session_runtime_state(current_config, peer_agent),
            fingerprint=_peer_fingerprint(peer_id),
        )
        setattr(peer_agent, "current_session_id", sid)

    def _chat(peer_id: str, prompt: str) -> ChatResponse:
        peer_agent = _create_peer_agent(peer_id)
        runtime_agent_id = f"chat:{uuid.uuid4().hex[:12]}"
        setattr(peer_agent, "runtime_agent_id", runtime_agent_id)
        try:
            with agent_runtime_limiter.agent_slot(
                runtime_agent_id,
                agent_type="chat",
                label=peer_id,
                is_cancelled=peer_agent.stop_requested,
            ):
                response = peer_agent.chat(prompt)
            _save_peer_session(peer_agent, peer_id)
            return ChatResponse(response=response)
        except Exception as exc:
            _save_peer_session(peer_agent, peer_id)
            return ChatResponse(response="", error=str(exc))

    def _agent_chat(agent_obj: Any, prompt: str, *, clear_stop_request: bool) -> str:
        signature = inspect.signature(agent_obj.chat)
        if "clear_stop_request" in signature.parameters:
            return agent_obj.chat(prompt, clear_stop_request=clear_stop_request)
        return agent_obj.chat(prompt)

    def _stream_chat(peer_id: str, prompt: str, remote_session) -> None:
        peer_agent = _create_peer_agent(
            peer_id,
            session_hint=getattr(remote_session, "session_hint", None),
            resume_latest=False,
        )
        remote_session.set_cancel_callback(
            lambda reason: (
                peer_agent.request_stop(),
                relay_server.cancel_pending_requests(peer_id, reason),
            )
        )
        if getattr(remote_session, "cancel_requested", False):
            peer_agent.request_stop()

        runtime_agent_id = f"chat:{getattr(remote_session, 'chat_id', uuid.uuid4().hex)}"
        setattr(peer_agent, "runtime_agent_id", runtime_agent_id)

        def _emit_runtime_status(payload: dict[str, Any]) -> None:
            remote_session.append_event("runtime_status", payload)

        try:
            agent_runtime_limiter.acquire_agent_slot(
                runtime_agent_id,
                agent_type="chat",
                label=peer_id,
                is_cancelled=lambda: bool(getattr(remote_session, "cancel_requested", False)),
                on_wait=_emit_runtime_status,
            )
        except AgentRuntimeCancelled:
            remote_session.append_event(
                "chat_cancelled",
                {"reason": getattr(remote_session, "cancel_reason", "user_cancelled")},
            )
            remote_session.append_event("chat_end", {"response": ""})
            return

        session_id = getattr(peer_agent, "current_session_id", "-") or "-"
        peer_info = relay_server.registry.get(peer_id)
        connection_marker = (
            f"{getattr(peer_info, 'connected_at', 0):.6f}"
            if peer_info is not None
            else "0"
        )
        startup_key = (peer_id, str(session_id), connection_marker)
        if startup_key not in startup_announced:
            remote_session.append_event(
                "remote_peer_ready",
                {
                    "peer_id": peer_id,
                    "session_id": session_id,
                    "fingerprint": _peer_fingerprint(peer_id),
                    "mode": getattr(peer_agent, "active_mode", "-") or "-",
                    "model": getattr(getattr(peer_agent, "llm", None), "model", "-")
                    or "-",
                    "workspace_root": getattr(peer_info, "workspace_root", None)
                    if peer_info is not None
                    else None,
                },
            )
            startup_announced.add(startup_key)

        current_config = _current_config()
        if prompt.strip().startswith("/") and current_config is not None:
            command_bus = UIEventBus()
            command_result = handle_command(
                prompt.strip(),
                peer_agent,
                current_config,
                getattr(peer_agent, "current_session_id", None),
                command_bus,
                CLI_PROFILE,
                runner.dependencies.create_action_registry(),
                sessions_dir,
                skills_service,
            )
            if command_result["action"] != "chat":
                setattr(peer_agent, "current_session_id", command_result["session_id"])

                for event in getattr(command_bus, "_history", []):
                    remote_session.append_event(
                        _structured_ui_event_type(event),
                        _structured_ui_event_payload(event),
                    )

                if command_result["action"] == "exit":
                    remote_session.append_event(
                        "output",
                        {
                            "format": "plain",
                            "content": "Exit command received. Use Ctrl+C to terminate remote peer.\n",
                        },
                    )
                _save_peer_session(peer_agent, peer_id)
                remote_session.append_event("chat_end", {"response": ""})
                agent_runtime_limiter.release_agent_slot(runtime_agent_id)
                return

        ansi_console = Console(
            record=True, force_terminal=True, color_system="truecolor"
        )
        renderer = CLIRenderer(console_override=ansi_console)
        assistant_content_emitted = {"value": False}
        active_tool_calls_by_name: dict[str, list[str]] = {}

        def _flush_output() -> None:
            rendered = ansi_console.export_text(clear=True, styles=True)
            if rendered.strip():
                remote_session.append_event(
                    "output", {"format": "terminal", "content": rendered}
                )

        def _remote_backend() -> RemoteRelayToolBackend | None:
            for tool in getattr(peer_agent, "tools", []):
                backend = getattr(tool, "backend", None)
                if isinstance(backend, RemoteRelayToolBackend):
                    return backend
            return None

        def _peer_supports_tool_preview() -> bool:
            peer = relay_server.registry.get(peer_id)
            return bool(peer and "tool_preview" in peer.capabilities)

        def _args_section(request: ApprovalRequest) -> dict[str, Any] | None:
            if not request.tool_args:
                return None
            return {
                "id": "args",
                "title": "Arguments",
                "kind": "json",
                "content": request.tool_args,
            }

        def _section_markdown(section: dict[str, Any]) -> str:
            title = str(section.get("title") or "Details")
            kind = str(section.get("kind") or "text")
            content = section.get("content", "")
            if kind == "diff":
                return f"### {title}\n\n```diff\n{content}\n```"
            if kind == "json":
                return (
                    f"### {title}\n\n```json\n"
                    f"{json.dumps(content, ensure_ascii=False, indent=2)}\n```"
                )
            return f"### {title}\n\n{content}"

        def _preview_state(preview: ToolPreviewResult) -> dict[str, Any]:
            state: dict[str, Any] = {}
            if preview.resolved_path is not None:
                state["resolved_path"] = preview.resolved_path
            if preview.old_sha256 is not None:
                state["old_sha256"] = preview.old_sha256
            if preview.old_exists is not None:
                state["old_exists"] = preview.old_exists
            if preview.old_size is not None:
                state["old_size"] = preview.old_size
            if preview.old_mtime_ns is not None:
                state["old_mtime_ns"] = preview.old_mtime_ns
            return state

        def _build_remote_preview(
            request: ApprovalRequest,
        ) -> tuple[list[dict[str, Any]], ToolPreviewResult | None, str | None]:
            backend = _remote_backend()
            if (
                backend is None
                or request.tool_name not in {"write_file", "edit_file"}
                or not _peer_supports_tool_preview()
            ):
                section = _args_section(request)
                return ([section] if section else []), None, "preview_unavailable"

            preview = backend.preview_tool(request.tool_name, dict(request.tool_args))
            if preview.ok:
                if preview.sections:
                    return preview.sections, preview, None
                if preview.diff:
                    return (
                        [
                            {
                                "id": "diff",
                                "title": "Proposed file diff",
                                "kind": "diff",
                                "content": preview.diff,
                                "path": preview.resolved_path,
                                "resolved_path": preview.resolved_path,
                                "original_text": preview.original_text,
                                "modified_text": preview.modified_text,
                            }
                        ],
                        preview,
                        None,
                    )

            sections: list[dict[str, Any]] = []
            section = _args_section(request)
            if section is not None:
                sections.append(section)
            sections.append(
                {
                    "id": "preview",
                    "title": "Preview unavailable",
                    "kind": "text",
                    "content": preview.error_message
                    or preview.error_code
                    or "Peer could not build a preview.",
                }
            )
            return sections, preview, preview.error_message or "preview_unavailable"

        class _RemoteApprovalProvider(ApprovalProvider):
            def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
                return self._request_remote_decision(request)

            @property
            def handler(self) -> Callable[[PendingApproval], None]:
                return self._handle_pending_approval

            def _handle_pending_approval(self, pending: PendingApproval) -> None:
                pending.resolve(self._request_remote_decision(pending.request))

            def _request_remote_decision(
                self, request: ApprovalRequest
            ) -> ApprovalDecision:
                approval_id = str(uuid.uuid4())
                tool_call_id = str(request.metadata.get("tool_call_id") or "")
                remote_session.register_approval(approval_id)
                sections, preview, preview_error = _build_remote_preview(request)
                payload = {
                    "approval_id": approval_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": request.tool_name,
                    "tool_source": request.tool_source,
                    "reason": request.reason,
                    "tool_args": request.tool_args,
                    "sections": sections,
                    "preview_unavailable": preview is None or not preview.ok,
                    "preview_error": preview_error,
                    "format": "markdown",
                    "content": "\n\n".join(
                        part
                        for part in [
                            f"## Approval required: {request.tool_name}",
                            f"Tool `{request.tool_name}` from source `{request.tool_source}` requires approval.",
                            request.reason or "",
                            *[_section_markdown(section) for section in sections],
                        ]
                        if part
                    ),
                }
                remote_session.append_event("approval_request", payload)
                decision, reason = remote_session.wait_approval(approval_id)
                remote_session.append_event(
                    "approval_resolved",
                    {
                        "approval_id": approval_id,
                        "tool_call_id": tool_call_id,
                        "decision": decision,
                        "reason": reason,
                    },
                )
                if decision == "allow_once":
                    backend = _remote_backend()
                    if backend is not None and preview is not None and preview.ok:
                        backend.remember_approved_preview(
                            request.tool_name,
                            dict(request.tool_args),
                            _preview_state(preview),
                        )
                    return ApprovalDecision.allow_once(reason)
                return ApprovalDecision.deny_once(reason)

        def _on_remote_stream(
            tool_name: str, chunk: Any, tool_call_id: str | None = None
        ) -> None:
            resolved_tool_call_id = tool_call_id
            if not resolved_tool_call_id:
                candidates = active_tool_calls_by_name.get(tool_name) or []
                if candidates:
                    resolved_tool_call_id = candidates[-1]
            remote_session.append_event(
                "tool_call_stream",
                {
                    "tool_name": tool_name,
                    "tool_call_id": resolved_tool_call_id,
                    "format": "plain",
                    "stream": getattr(chunk, "chunk_type", "stdout"),
                    "content": getattr(chunk, "data", ""),
                    "meta": getattr(chunk, "meta", {}),
                },
            )

        def _on_agent_event(event: AgentEvent) -> None:
            if event.event_type == AgentEventType.STREAM_TOKEN:
                content = event.data.get("token", "")
                if content:
                    assistant_content_emitted["value"] = True
                    remote_session.append_event(
                        "assistant_delta",
                        {"format": "markdown", "content": content},
                    )
                return
            if event.event_type == AgentEventType.USAGE_UPDATE:
                remote_session.append_event("usage_update", event.data)
                return
            if event.event_type == AgentEventType.RUNTIME_STATUS:
                remote_session.append_event("runtime_status", event.data)
                return
            if event.event_type == AgentEventType.CHAT_END:
                response = event.data.get("response", "")
                if event.data.get("render_response", True) and response:
                    assistant_content_emitted["value"] = True
                    remote_session.append_event(
                        "assistant_message",
                        {"format": "markdown", "content": response},
                    )
                return
            if event.event_type == AgentEventType.TOOL_CALL_START:
                if event.tool_name and event.tool_call_id:
                    active_tool_calls_by_name.setdefault(event.tool_name, []).append(
                        event.tool_call_id
                    )
                remote_session.append_event(
                    "tool_call_start",
                    {
                        "tool_name": event.tool_name,
                        "tool_call_id": event.tool_call_id,
                        "tool_args": event.tool_args or {},
                        "tool_source": event.data.get("tool_source"),
                        "started_at": event.timestamp,
                    },
                )
                return
            elif event.event_type == AgentEventType.TOOL_CALL_END:
                if event.tool_name and event.tool_call_id:
                    candidates = active_tool_calls_by_name.get(event.tool_name)
                    if candidates and event.tool_call_id in candidates:
                        candidates.remove(event.tool_call_id)
                remote_session.append_event(
                    "tool_call_end",
                    {
                        "tool_name": event.tool_name,
                        "tool_call_id": event.tool_call_id,
                        "tool_success": event.tool_success,
                        "tool_result": event.tool_result or "",
                        "tool_source": event.data.get("tool_source"),
                        "meta": event.data.get("meta") or {},
                        "ended_at": event.timestamp,
                    },
                )
                return
            elif event.event_type == AgentEventType.ERROR:
                remote_session.append_event(
                    "error", {"message": event.error_message or "unknown error"}
                )
                return
            elif event.event_type == AgentEventType.SUBAGENT_COMPLETED:
                remote_session.append_event("subagent_completed", event.data)
                return

        previous_approval = peer_agent.approval_provider
        peer_agent.add_event_handler(_on_agent_event)
        peer_agent.approval_provider = _RemoteApprovalProvider()
        try:
            result = _agent_chat(
                peer_agent,
                prompt,
                clear_stop_request=not getattr(
                    remote_session, "cancel_requested", False
                ),
            )
            _flush_output()
            _save_peer_session(peer_agent, peer_id)
            if getattr(remote_session, "cancel_requested", False):
                remote_session.append_event(
                    "chat_cancelled",
                    {"reason": getattr(remote_session, "cancel_reason", None)},
                )
            remote_session.append_event(
                "chat_end",
                {
                    "response": result,
                    "response_rendered": assistant_content_emitted["value"],
                },
            )
        except Exception as exc:
            _flush_output()
            _save_peer_session(peer_agent, peer_id)
            remote_session.append_event("error", {"message": str(exc)})
        finally:
            peer_agent.approval_provider = previous_approval
            try:
                peer_agent._event_handlers.remove(_on_agent_event)
            except ValueError:
                pass
            agent_runtime_limiter.release_agent_slot(runtime_agent_id)
            renderer.close()

    runner._relay_http_service.set_chat_handler(_chat)
    runner._relay_http_service.set_stream_chat_handler(_stream_chat)


def _structured_ui_event_type(event) -> str:
    return {
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
    }.get(event.kind, "ui_event")


def _structured_ui_event_payload(event) -> dict[str, Any]:
    return {
        "message": event.message,
        "level": event.level.value,
        "kind": event.kind.value,
        "timestamp": event.timestamp,
        **dict(event.data),
    }
