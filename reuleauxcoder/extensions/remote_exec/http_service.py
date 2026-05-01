"""HTTP transport adapter for the remote relay host."""

from __future__ import annotations

import json
import queue
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse

from reuleauxcoder.extensions.remote_exec.bootstrap import (
    generate_bootstrap_script,
    generate_powershell_bootstrap_script,
)
from reuleauxcoder.extensions.remote_exec.admin import (
    ConfigReloadHandler,
    ProviderModelsHandler,
    ProviderTestHandler,
    RemoteAdminConfigManager,
)
from reuleauxcoder.extensions.remote_exec.errors import RegisterRejectedError
from reuleauxcoder.extensions.remote_exec.protocol import (
    ApprovalReplyRequest,
    ApprovalReplyResponse,
    ChatCancelRequest,
    ChatCancelResponse,
    ChatRequest,
    ChatResponse,
    ChatStartRequest,
    ChatStartResponse,
    ChatStreamRequest,
    ChatStreamResponse,
    CleanupResult,
    DisconnectNotice,
    EnvironmentCLIToolManifest,
    EnvironmentMCPServerManifest,
    EnvironmentManifestRequest,
    EnvironmentManifestResponse,
    EnvironmentSkillManifest,
    ExecToolResult,
    Heartbeat,
    MCPArtifactManifest,
    MCPLaunchManifest,
    MCPManifestRequest,
    MCPManifestResponse,
    MCPServerManifest,
    PeerMCPToolsReport,
    RegisterRejected,
    RegisterRequest,
    RelayEnvelope,
    SessionDeleteRequest,
    SessionListRequest,
    SessionLoadRequest,
    SessionNewRequest,
    SessionSnapshotRequest,
    ToolPreviewResult,
)
from reuleauxcoder.extensions.remote_exec.server import RelayServer
from reuleauxcoder.interfaces.events import UIEventBus, UIEventKind


@dataclass
class _RemoteChatSession:
    chat_id: str
    peer_id: str
    session_hint: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    running: bool = False
    seq_next: int = 1
    approval_waiters: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)
    cancel_requested: bool = False
    cancel_reason: str | None = None
    cancel_callback: Callable[[str], None] | None = None

    def append_event(
        self, event_type: str, payload: dict[str, Any] | None = None
    ) -> int:
        with self.cond:
            seq = self.seq_next
            self.seq_next += 1
            self.events.append(
                {
                    "chat_id": self.chat_id,
                    "seq": seq,
                    "type": event_type,
                    "payload": payload or {},
                }
            )
            self.cond.notify_all()
            return seq

    def wait_events(
        self, cursor: int, timeout_sec: float
    ) -> tuple[list[dict[str, Any]], bool, int]:
        deadline = time.time() + max(timeout_sec, 0.0)
        with self.cond:
            while cursor >= len(self.events) and not self.done:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self.cond.wait(timeout=remaining)
            out = self.events[cursor:]
            return out, self.done, len(self.events)

    def mark_running(self) -> None:
        with self.cond:
            self.running = True

    def mark_done(self) -> None:
        with self.cond:
            self.running = False
            self.done = True
            self.finished_at = time.time()
            for waiter in self.approval_waiters.values():
                if waiter.get("done"):
                    continue
                waiter["done"] = True
                waiter["decision"] = "deny_once"
                waiter["reason"] = "chat_closed"
            self.cond.notify_all()

    def set_cancel_callback(self, callback: Callable[[str], None]) -> None:
        call_immediately = False
        reason = "chat_cancelled"
        with self.cond:
            self.cancel_callback = callback
            if self.cancel_requested:
                call_immediately = True
                reason = self.cancel_reason or reason
        if call_immediately:
            callback(reason)

    def request_cancel(self, reason: str = "chat_cancelled") -> bool:
        callback: Callable[[str], None] | None
        first_request = False
        with self.cond:
            if not self.cancel_requested:
                first_request = True
            self.cancel_requested = True
            self.cancel_reason = reason
            for waiter in self.approval_waiters.values():
                if waiter.get("done"):
                    continue
                waiter["done"] = True
                waiter["decision"] = "deny_once"
                waiter["reason"] = reason
            callback = self.cancel_callback
            self.cond.notify_all()
        if callback is not None:
            callback(reason)
        return first_request

    def register_approval(self, approval_id: str) -> None:
        with self.cond:
            self.approval_waiters[approval_id] = {}

    def resolve_approval(
        self, approval_id: str, decision: str, reason: str | None
    ) -> bool:
        with self.cond:
            waiter = self.approval_waiters.get(approval_id)
            if waiter is None:
                return False
            waiter["done"] = True
            waiter["decision"] = decision
            waiter["reason"] = reason
            self.cond.notify_all()
            return True

    def wait_approval(
        self, approval_id: str, timeout_sec: float | None = None
    ) -> tuple[str, str | None]:
        deadline = time.time() + timeout_sec if timeout_sec else None
        with self.cond:
            waiter = self.approval_waiters.setdefault(approval_id, {})
            while not waiter.get("done"):
                if deadline is None:
                    self.cond.wait(timeout=0.5)
                    continue
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self.cond.wait(timeout=remaining)
            decision = str(waiter.get("decision", "deny_once"))
            reason = waiter.get("reason")
            self.approval_waiters.pop(approval_id, None)
            return decision, reason if isinstance(reason, str) else None

    def cancel_pending_approvals(self, reason: str) -> None:
        with self.cond:
            for waiter in self.approval_waiters.values():
                if waiter.get("done"):
                    continue
                waiter["done"] = True
                waiter["decision"] = "deny_once"
                waiter["reason"] = reason
            self.cond.notify_all()


class RemoteRelayHTTPService:
    """Expose ``RelayServer`` over a minimal HTTP API for remote peers."""

    def __init__(
        self,
        relay_server: RelayServer,
        bind: str,
        *,
        ui_bus: UIEventBus | None = None,
        artifact_provider: callable | None = None,
        chat_handler: Callable[[str, str], ChatResponse] | None = None,
        stream_chat_handler: Callable[[str, str, _RemoteChatSession], None]
        | None = None,
        session_handler: Callable[[str, str, dict[str, Any]], dict[str, Any]]
        | None = None,
        bootstrap_access_secret: str = "",
        admin_access_secret: str = "",
        bootstrap_token_ttl_sec: int = 300,
        mcp_servers: list[Any] | None = None,
        mcp_artifact_root: str | Path = ".rcoder/mcp-artifacts",
        environment_cli_tools: dict[str, Any] | None = None,
        environment_skills: dict[str, Any] | None = None,
        admin_config_path: str | Path | None = None,
        admin_config_reload_handler: ConfigReloadHandler | None = None,
        admin_provider_test_handler: ProviderTestHandler | None = None,
        admin_provider_models_handler: ProviderModelsHandler | None = None,
    ) -> None:
        self.relay_server = relay_server
        self.bind = bind
        self.ui_bus = ui_bus
        self.artifact_provider = artifact_provider
        self.chat_handler = chat_handler
        self.stream_chat_handler = stream_chat_handler
        self.session_handler = session_handler
        self.bootstrap_access_secret = bootstrap_access_secret
        self.admin_access_secret = admin_access_secret
        self.bootstrap_token_ttl_sec = bootstrap_token_ttl_sec
        self.mcp_servers = list(mcp_servers or [])
        self.mcp_artifact_root = Path(mcp_artifact_root)
        self.environment_cli_tools = dict(environment_cli_tools or {})
        self.environment_skills = dict(environment_skills or {})
        self.admin_manager = RemoteAdminConfigManager(
            Path(admin_config_path) if admin_config_path is not None else None,
            reload_handler=admin_config_reload_handler,
            provider_test_handler=admin_provider_test_handler,
            provider_models_handler=admin_provider_models_handler,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._queues: dict[str, queue.Queue[RelayEnvelope]] = {}
        self._queues_lock = threading.Lock()
        self._peer_chat_locks: dict[str, threading.Lock] = {}
        self._peer_chat_locks_lock = threading.Lock()
        self._chat_sessions: dict[str, _RemoteChatSession] = {}
        self._chat_sessions_lock = threading.Lock()
        self._chat_session_ttl_sec = 300.0
        self.relay_server._send_fn = self._enqueue_outbound

    @property
    def base_url(self) -> str:
        host, port = _parse_bind(self.bind)
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        host, port = _parse_bind(self.bind)
        handler_cls = self._build_handler()
        self._server = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        if self.ui_bus is not None:
            self.ui_bus.info(
                f"Remote relay HTTP service listening on {self.base_url}",
                kind=UIEventKind.REMOTE,
            )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self._server = None

    def issue_bootstrap_token(self, ttl_sec: int = 300) -> str:
        return self.relay_server.issue_bootstrap_token(ttl_sec=ttl_sec)

    def set_chat_handler(
        self, handler: Callable[[str, str], ChatResponse] | None
    ) -> None:
        self.chat_handler = handler

    def set_stream_chat_handler(
        self,
        handler: Callable[[str, str, _RemoteChatSession], None] | None,
    ) -> None:
        self.stream_chat_handler = handler

    def set_session_handler(
        self,
        handler: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None,
    ) -> None:
        self.session_handler = handler

    def _create_chat_session(
        self, peer_id: str, session_hint: str | None = None
    ) -> _RemoteChatSession:
        self._gc_chat_sessions()
        session = _RemoteChatSession(
            chat_id=str(uuid.uuid4()), peer_id=peer_id, session_hint=session_hint
        )
        with self._chat_sessions_lock:
            self._chat_sessions[session.chat_id] = session
        return session

    def _gc_chat_sessions(self) -> None:
        now = time.time()
        with self._chat_sessions_lock:
            stale_ids = [
                chat_id
                for chat_id, session in self._chat_sessions.items()
                if session.done
                and session.finished_at is not None
                and now - session.finished_at > self._chat_session_ttl_sec
            ]
            for chat_id in stale_ids:
                self._chat_sessions.pop(chat_id, None)

    def _get_chat_session(self, chat_id: str) -> _RemoteChatSession | None:
        self._gc_chat_sessions()
        with self._chat_sessions_lock:
            return self._chat_sessions.get(chat_id)

    def _get_peer_chat_lock(self, peer_id: str) -> threading.Lock:
        with self._peer_chat_locks_lock:
            return self._peer_chat_locks.setdefault(peer_id, threading.Lock())

    def _abort_peer_chat_sessions(self, peer_id: str, reason: str) -> None:
        with self._chat_sessions_lock:
            peer_sessions = [
                session
                for session in self._chat_sessions.values()
                if session.peer_id == peer_id and not session.done
            ]
        for session in peer_sessions:
            session.cancel_pending_approvals(reason)
            session.append_event("error", {"message": reason})
            session.mark_done()

    def _enqueue_outbound(self, peer_id: str, envelope: RelayEnvelope) -> None:
        with self._queues_lock:
            peer_queue = self._queues.setdefault(peer_id, queue.Queue())
        peer_queue.put(envelope)

    def _next_envelope(self, peer_id: str) -> RelayEnvelope | None:
        with self._queues_lock:
            peer_queue = self._queues.setdefault(peer_id, queue.Queue())
        try:
            return peer_queue.get_nowait()
        except queue.Empty:
            return None

    def _build_handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/remote/capabilities":
                    self._handle_capabilities()
                    return
                if parsed.path == "/remote/bootstrap.sh":
                    self._handle_bootstrap(parsed, "sh")
                    return
                if parsed.path == "/remote/bootstrap.ps1":
                    self._handle_bootstrap(parsed, "ps1")
                    return
                if parsed.path.startswith("/remote/artifacts/"):
                    self._handle_artifact(parsed.path)
                    return
                if parsed.path.startswith("/remote/mcp/artifacts/"):
                    self._handle_mcp_artifact(parsed.path)
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/remote/register":
                    self._handle_register()
                    return
                if parsed.path == "/remote/heartbeat":
                    self._handle_heartbeat()
                    return
                if parsed.path == "/remote/poll":
                    self._handle_poll()
                    return
                if parsed.path == "/remote/result":
                    self._handle_result()
                    return
                if parsed.path == "/remote/mcp/manifest":
                    self._handle_mcp_manifest()
                    return
                if parsed.path == "/remote/mcp/tools":
                    self._handle_mcp_tools()
                    return
                if parsed.path == "/remote/environment/manifest":
                    self._handle_environment_manifest()
                    return
                if parsed.path == "/remote/disconnect":
                    self._handle_disconnect()
                    return
                if parsed.path == "/remote/chat":
                    self._handle_chat()
                    return
                if parsed.path == "/remote/chat/start":
                    self._handle_chat_start()
                    return
                if parsed.path == "/remote/chat/stream":
                    self._handle_chat_stream()
                    return
                if parsed.path == "/remote/chat/cancel":
                    self._handle_chat_cancel()
                    return
                if parsed.path == "/remote/approval/reply":
                    self._handle_approval_reply()
                    return
                if parsed.path.startswith("/remote/sessions/"):
                    self._handle_sessions(parsed.path)
                    return
                if parsed.path.startswith("/remote/admin/"):
                    self._handle_admin(parsed.path)
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def _read_json(self) -> dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0:
                    return {}
                raw = self.rfile.read(content_length)
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(
                self,
                status: int,
                body: str,
                content_type: str = "text/plain; charset=utf-8",
            ) -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_bytes(
                self,
                status: int,
                content: bytes,
                content_type: str = "application/octet-stream",
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _verify_peer_token(self, peer_token: Any) -> str | None:
                if not isinstance(peer_token, str) or not peer_token:
                    return None
                return service.relay_server.token_manager.verify_peer_token(peer_token)

            def _handle_capabilities(self) -> None:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "api_version": 1,
                        "server_version": _package_version(),
                        "capabilities": {
                            "sessions": service.session_handler is not None,
                            "chat_stream": service.stream_chat_handler is not None,
                            "fresh_session_without_session_hint": service.stream_chat_handler
                            is not None,
                            "peer_token_heartbeat_refresh": True,
                        },
                    },
                )

            def _verify_admin_secret(self) -> bool:
                configured_secret = service.admin_access_secret
                presented_secret = self.headers.get("X-RC-Admin-Secret", "")
                return bool(configured_secret) and secrets.compare_digest(
                    presented_secret, configured_secret
                )

            def _handle_admin(self, path: str) -> None:
                if not service.admin_access_secret:
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "admin_disabled"})
                    return
                if not self._verify_admin_secret():
                    self._send_json(
                        HTTPStatus.FORBIDDEN, {"error": "invalid_admin_secret"}
                    )
                    return
                payload = self._read_json()
                try:
                    if path == "/remote/admin/status":
                        result = {"ok": True, **service.admin_manager.status()}
                        self._send_json(HTTPStatus.OK, result)
                        return
                    if path == "/remote/admin/server-settings/read":
                        result = {
                            "ok": True,
                            **service.admin_manager.read_server_settings(),
                        }
                        self._send_json(HTTPStatus.OK, result)
                        return
                    if path == "/remote/admin/server-settings/update":
                        result = service.admin_manager.update_server_settings(payload)
                        self._send_json(result.status, result.payload)
                        return
                    if path == "/remote/admin/providers/list":
                        result = {"ok": True, **service.admin_manager.list_providers()}
                        self._send_json(HTTPStatus.OK, result)
                        return
                    if path == "/remote/admin/providers/record":
                        result = service.admin_manager.record_provider(payload)
                    elif path == "/remote/admin/providers/test":
                        result = service.admin_manager.test_provider(payload)
                    elif path == "/remote/admin/providers/delete":
                        result = service.admin_manager.delete_provider(payload)
                    elif path == "/remote/admin/providers/copy":
                        result = service.admin_manager.copy_provider(payload)
                    elif path == "/remote/admin/providers/enable":
                        result = service.admin_manager.enable_provider(payload)
                    elif path == "/remote/admin/providers/models":
                        result = service.admin_manager.list_provider_models(payload)
                    elif path == "/remote/admin/models/list":
                        result = {
                            "ok": True,
                            **service.admin_manager.list_model_profiles(),
                        }
                        self._send_json(HTTPStatus.OK, result)
                        return
                    elif path == "/remote/admin/models/record":
                        result = service.admin_manager.record_model_profile(payload)
                    elif path == "/remote/admin/models/activate":
                        result = service.admin_manager.activate_model_profile(payload)
                    elif path == "/remote/admin/toolchains/list":
                        result = {
                            "ok": True,
                            **service.admin_manager.list_toolchains(),
                        }
                        self._send_json(HTTPStatus.OK, result)
                        return
                    elif path == "/remote/admin/toolchains/dashboard":
                        result = {
                            "ok": True,
                            **service.admin_manager.toolchain_dashboard(),
                        }
                        self._send_json(HTTPStatus.OK, result)
                        return
                    elif path == "/remote/admin/toolchains/record":
                        result = service.admin_manager.record_toolchain(payload)
                    elif path == "/remote/admin/toolchains/delete":
                        result = service.admin_manager.delete_toolchain(payload)
                    elif path == "/remote/admin/toolchains/enable":
                        result = service.admin_manager.enable_toolchain(payload)
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                except Exception as exc:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "admin_request_failed", "message": str(exc)},
                    )
                    return
                self._send_json(result.status, result.payload)

            def _handle_bootstrap(self, parsed, script_kind: str) -> None:
                del parsed
                configured_secret = service.bootstrap_access_secret
                presented_secret = self.headers.get("X-RC-Bootstrap-Secret", "")
                if not configured_secret:
                    self._send_json(
                        HTTPStatus.FORBIDDEN, {"error": "bootstrap_disabled"}
                    )
                    return
                if not secrets.compare_digest(presented_secret, configured_secret):
                    self._send_json(
                        HTTPStatus.FORBIDDEN, {"error": "invalid_bootstrap_secret"}
                    )
                    return
                token = service.issue_bootstrap_token(
                    ttl_sec=service.bootstrap_token_ttl_sec
                )
                host_header = self.headers.get("Host")
                forwarded_proto = self.headers.get("X-Forwarded-Proto", "http")
                request_base_url = (
                    f"{forwarded_proto}://{host_header}"
                    if host_header
                    else service.base_url
                )
                if script_kind == "ps1":
                    script = generate_powershell_bootstrap_script(
                        request_base_url, token
                    )
                    content_type = "text/x-powershell; charset=utf-8"
                else:
                    script = generate_bootstrap_script(request_base_url, token)
                    content_type = "text/x-shellscript; charset=utf-8"
                self._send_text(
                    HTTPStatus.OK,
                    script,
                    content_type,
                )

            def _handle_artifact(self, path: str) -> None:
                if service.artifact_provider is None:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": "artifact_unavailable",
                            "message": "peer artifact not uploaded yet",
                        },
                    )
                    return
                suffix = path.removeprefix("/remote/artifacts/")
                parts = suffix.split("/")
                if len(parts) != 3:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                os_name, arch, artifact_name = parts
                try:
                    artifact = service.artifact_provider(os_name, arch, artifact_name)
                except RuntimeError as exc:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "artifact_unavailable", "message": str(exc)},
                    )
                    return
                if artifact is None:
                    self._send_json(
                        HTTPStatus.NOT_FOUND, {"error": "artifact_unavailable"}
                    )
                    return
                content, content_type = artifact
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _handle_mcp_manifest(self) -> None:
                payload = self._read_json()
                try:
                    req = MCPManifestRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_mcp_manifest_request"}
                    )
                    return
                peer_id = self._verify_peer_token(req.peer_token)
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                response = service._build_mcp_manifest(req.os, req.arch)
                self._send_json(HTTPStatus.OK, response.to_dict())

            def _handle_mcp_artifact(self, path: str) -> None:
                peer_token = self.headers.get("X-RC-Peer-Token", "")
                peer_id = self._verify_peer_token(peer_token)
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                suffix = unquote(path.removeprefix("/remote/mcp/artifacts/"))
                artifact_path = service._resolve_mcp_artifact_path(suffix)
                if artifact_path is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                try:
                    content = artifact_path.read_bytes()
                except OSError as exc:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "artifact_unavailable", "message": str(exc)},
                    )
                    return
                self._send_bytes(HTTPStatus.OK, content)

            def _handle_mcp_tools(self) -> None:
                payload = self._read_json()
                try:
                    report = PeerMCPToolsReport.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_mcp_tools_report"}
                    )
                    return
                peer_id = self._verify_peer_token(report.peer_token)
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                ok = service.relay_server.update_peer_mcp_tools(
                    peer_id, report.tools, report.diagnostics
                )
                if service.ui_bus is not None:
                    service.ui_bus.info(
                        f"Remote peer MCP tools reported: {len(report.tools)}",
                        kind=UIEventKind.REMOTE,
                        peer_id=peer_id,
                    )
                self._send_json(HTTPStatus.OK, {"ok": ok, "peer_id": peer_id})

            def _handle_environment_manifest(self) -> None:
                payload = self._read_json()
                try:
                    req = EnvironmentManifestRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_environment_manifest_request"},
                    )
                    return
                peer_id = self._verify_peer_token(req.peer_token)
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                response = service._build_environment_manifest(
                    req.os, req.arch, req.workspace
                )
                self._send_json(HTTPStatus.OK, response.to_dict())

            def _handle_register(self) -> None:
                payload = self._read_json()
                try:
                    resp = service.relay_server._on_register(
                        RegisterRequest.from_dict(payload)
                    )
                except RegisterRejectedError as exc:
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "type": "register_rejected",
                            "payload": RegisterRejected(reason=exc.message).to_dict(),
                        },
                    )
                    return
                service.ui_bus and service.ui_bus.success(
                    f"Remote peer registered: {resp.peer_id}",
                    kind=UIEventKind.REMOTE,
                    peer_id=resp.peer_id,
                )
                self._send_json(
                    HTTPStatus.OK, {"type": "register_ok", "payload": resp.to_dict()}
                )

            def _handle_heartbeat(self) -> None:
                payload = self._read_json()
                hb = Heartbeat.from_dict(payload)
                peer_id = service.relay_server.token_manager.refresh_peer_token(
                    hb.peer_token, ttl_sec=service.relay_server.peer_token_ttl_sec
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                self._send_json(HTTPStatus.OK, {"ok": True, "peer_id": peer_id})

            def _handle_poll(self) -> None:
                payload = self._read_json()
                peer_token = payload.get("peer_token")
                if not isinstance(peer_token, str) or not peer_token:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "peer_token_required"}
                    )
                    return
                peer_id = service.relay_server.token_manager.verify_peer_token(
                    peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                service.relay_server.registry.update_heartbeat(peer_id)
                env = service._next_envelope(peer_id)
                if env is None:
                    self._send_json(HTTPStatus.OK, {"type": "noop", "payload": {}})
                    return
                self._send_json(HTTPStatus.OK, env.to_dict())

            def _handle_result(self) -> None:
                payload = self._read_json()
                peer_token = payload.get("peer_token")
                request_id = payload.get("request_id")
                result_type = payload.get("type", "tool_result")
                result_payload = payload.get("payload", {})
                peer_id = service.relay_server.token_manager.verify_peer_token(
                    peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                if not isinstance(request_id, str) or not request_id:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "request_id_required"}
                    )
                    return
                if result_type == "cleanup_result":
                    result = CleanupResult.from_dict(result_payload)
                    env = RelayEnvelope(
                        type="cleanup_result",
                        request_id=request_id,
                        peer_id=peer_id,
                        payload=result.to_dict(),
                    )
                elif result_type == "tool_stream":
                    env = RelayEnvelope(
                        type="tool_stream",
                        request_id=request_id,
                        peer_id=peer_id,
                        payload=result_payload,
                    )
                elif result_type == "tool_preview_result":
                    result = ToolPreviewResult.from_dict(result_payload)
                    env = RelayEnvelope(
                        type="tool_preview_result",
                        request_id=request_id,
                        peer_id=peer_id,
                        payload=result.to_dict(),
                    )
                else:
                    result = ExecToolResult.from_dict(result_payload)
                    env = RelayEnvelope(
                        type="tool_result",
                        request_id=request_id,
                        peer_id=peer_id,
                        payload=result.to_dict(),
                    )
                service.relay_server.handle_inbound(peer_id, env)
                self._send_json(HTTPStatus.OK, {"ok": True})

            def _handle_chat(self) -> None:
                payload = self._read_json()
                try:
                    req = ChatRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_chat_request"}
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    req.peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return

                if service.chat_handler is None:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        ChatResponse(response="", error="chat_unavailable").to_dict(),
                    )
                    return

                with service._get_peer_chat_lock(peer_id):
                    try:
                        response = service.chat_handler(peer_id, req.prompt)
                    except Exception as exc:
                        response = ChatResponse(response="", error=str(exc))

                self._send_json(HTTPStatus.OK, response.to_dict())

            def _handle_chat_start(self) -> None:
                payload = self._read_json()
                try:
                    req = ChatStartRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_chat_start_request"}
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    req.peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                if service.stream_chat_handler is None:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        ChatStartResponse(
                            chat_id="", error="chat_stream_unavailable"
                        ).to_dict(),
                    )
                    return

                session = service._create_chat_session(peer_id, req.session_hint)
                session.append_event("chat_start", {"prompt": req.prompt})
                session.mark_running()

                def _run_chat() -> None:
                    with service._get_peer_chat_lock(peer_id):
                        try:
                            service.stream_chat_handler(peer_id, req.prompt, session)
                        except Exception as exc:
                            session.append_event("error", {"message": str(exc)})
                        finally:
                            session.mark_done()

                threading.Thread(target=_run_chat, daemon=True).start()
                self._send_json(
                    HTTPStatus.OK, ChatStartResponse(chat_id=session.chat_id).to_dict()
                )

            def _handle_chat_stream(self) -> None:
                payload = self._read_json()
                try:
                    req = ChatStreamRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_chat_stream_request"}
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    req.peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                session = service._get_chat_session(req.chat_id)
                if session is None or session.peer_id != peer_id:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "chat_not_found"})
                    return

                events, done, next_cursor = session.wait_events(
                    req.cursor, req.timeout_sec
                )
                self._send_json(
                    HTTPStatus.OK,
                    ChatStreamResponse(
                        events=events, done=done, next_cursor=next_cursor
                    ).to_dict(),
                )

            def _handle_chat_cancel(self) -> None:
                payload = self._read_json()
                try:
                    req = ChatCancelRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_chat_cancel_request"},
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    req.peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                session = service._get_chat_session(req.chat_id)
                if session is None or session.peer_id != peer_id:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        ChatCancelResponse(
                            ok=False, error="chat_not_found"
                        ).to_dict(),
                    )
                    return

                reason = req.reason or "chat_cancelled"
                first_request = session.request_cancel(reason)
                if first_request:
                    session.append_event(
                        "chat_cancel_requested", {"reason": reason}
                    )
                self._send_json(
                    HTTPStatus.OK, ChatCancelResponse(ok=True).to_dict()
                )

            def _handle_sessions(self, path: str) -> None:
                payload = self._read_json()
                action = path.rsplit("/", 1)[-1]
                try:
                    if action == "list":
                        req = SessionListRequest.from_dict(payload)
                        peer_token = req.peer_token
                    elif action == "load":
                        req = SessionLoadRequest.from_dict(payload)
                        peer_token = req.peer_token
                    elif action == "new":
                        req = SessionNewRequest.from_dict(payload)
                        peer_token = req.peer_token
                    elif action == "delete":
                        req = SessionDeleteRequest.from_dict(payload)
                        peer_token = req.peer_token
                    elif action == "snapshot":
                        req = SessionSnapshotRequest.from_dict(payload)
                        peer_token = req.peer_token
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST, {"error": "invalid_session_request"}
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                if service.session_handler is None:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"ok": False, "error": "sessions_unavailable"},
                    )
                    return

                try:
                    result = dict(service.session_handler(action, peer_id, payload))
                    status = int(result.pop("_status", HTTPStatus.OK))
                except Exception as exc:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"ok": False, "error": str(exc)},
                    )
                    return
                self._send_json(status, result)

            def _handle_approval_reply(self) -> None:
                payload = self._read_json()
                try:
                    req = ApprovalReplyRequest.from_dict(payload)
                except Exception:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_approval_reply_request"},
                    )
                    return

                peer_id = service.relay_server.token_manager.verify_peer_token(
                    req.peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                session = service._get_chat_session(req.chat_id)
                if session is None or session.peer_id != peer_id:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        ApprovalReplyResponse(
                            ok=False, error="chat_not_found"
                        ).to_dict(),
                    )
                    return
                ok = session.resolve_approval(req.approval_id, req.decision, req.reason)
                if not ok:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        ApprovalReplyResponse(
                            ok=False, error="approval_not_found"
                        ).to_dict(),
                    )
                    return
                self._send_json(HTTPStatus.OK, ApprovalReplyResponse(ok=True).to_dict())

            def _handle_disconnect(self) -> None:
                payload = self._read_json()
                peer_token = payload.get("peer_token")
                peer_id = service.relay_server.token_manager.verify_peer_token(
                    peer_token
                )
                if peer_id is None:
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
                    )
                    return
                notice = DisconnectNotice(
                    reason=payload.get("reason", "peer_initiated")
                )
                service._abort_peer_chat_sessions(
                    peer_id, f"peer_disconnected: {notice.reason}"
                )
                service.relay_server.disconnect_peer(peer_id, notice.reason)
                service.ui_bus and service.ui_bus.warning(
                    f"Remote peer disconnected: {peer_id}",
                    kind=UIEventKind.REMOTE,
                    peer_id=peer_id,
                    reason=notice.reason,
                )
                self._send_json(HTTPStatus.OK, {"ok": True})

        return Handler

    def _mcp_artifact_root_abs(self) -> Path:
        root = self.mcp_artifact_root.expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    def _resolve_mcp_artifact_path(self, artifact_path: str) -> Path | None:
        if not artifact_path or artifact_path.startswith(("/", "\\")):
            return None
        root = self._mcp_artifact_root_abs()
        resolved = (root / artifact_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        if not resolved.exists() or not resolved.is_file():
            return None
        return resolved

    def _build_mcp_manifest(self, os_name: str, arch: str) -> MCPManifestResponse:
        platform = f"{os_name}-{arch}"
        servers: list[MCPServerManifest] = []
        diagnostics: list[dict[str, Any]] = []
        for server in self.mcp_servers:
            if not getattr(server, "enabled", True):
                continue
            if getattr(server, "placement", "server") not in {"peer", "both"}:
                continue
            server_name = getattr(server, "name", "")
            distribution = str(getattr(server, "distribution", "") or "").lower()
            if distribution not in {"command", "artifact"}:
                distribution = "artifact" if getattr(server, "artifacts", {}) else "command"
            version = getattr(server, "version", None)
            artifact_manifest: MCPArtifactManifest | None = None
            if distribution == "artifact":
                if not version:
                    diagnostics.append(
                        {
                            "server": server_name,
                            "level": "error",
                            "message": "peer MCP server is missing version",
                        }
                    )
                    continue
                artifacts = getattr(server, "artifacts", {}) or {}
                artifact = artifacts.get(platform)
                if artifact is None:
                    diagnostics.append(
                        {
                            "server": server_name,
                            "level": "error",
                            "message": f"peer MCP server has no artifact for {platform}",
                        }
                    )
                    continue
                artifact_path = getattr(artifact, "path", "")
                sha256 = getattr(artifact, "sha256", "")
                if not artifact_path or not sha256:
                    diagnostics.append(
                        {
                            "server": server_name,
                            "level": "error",
                            "message": f"peer MCP server artifact for {platform} is incomplete",
                        }
                    )
                    continue
                artifact_manifest = MCPArtifactManifest(
                    platform=platform,
                    path=artifact_path,
                    sha256=sha256,
                    url="/remote/mcp/artifacts/" + quote(artifact_path, safe="/"),
                )
                launch = getattr(artifact, "launch", None) or getattr(server, "launch", None)
            else:
                launch = getattr(server, "launch", None)
            if launch is None:
                command = getattr(server, "command", "")
                launch_args = list(getattr(server, "args", []) or [])
                launch_env = dict(getattr(server, "env", {}) or {})
                launch_cwd = getattr(server, "cwd", None)
            else:
                command = getattr(launch, "command", "")
                launch_args = list(getattr(launch, "args", []) or [])
                launch_env = dict(getattr(launch, "env", {}) or {})
                launch_cwd = getattr(launch, "cwd", None)
            if not command:
                diagnostics.append(
                    {
                        "server": server_name,
                        "level": "error",
                        "message": "peer MCP server launch command is empty",
                    }
                )
                continue
            servers.append(
                MCPServerManifest(
                    name=server_name,
                    version=str(version) if version is not None else "",
                    distribution=distribution,
                    artifact=artifact_manifest,
                    launch=MCPLaunchManifest(
                        command=command,
                        args=launch_args,
                        env=launch_env,
                        cwd=launch_cwd,
                    ),
                    permissions=dict(getattr(server, "permissions", {}) or {}),
                    requirements=dict(getattr(server, "requirements", {}) or {}),
                )
            )
        return MCPManifestResponse(servers=servers, diagnostics=diagnostics)

    def _build_environment_manifest(
        self, os_name: str, arch: str, workspace: str
    ) -> EnvironmentManifestResponse:
        del os_name, arch
        tools: list[EnvironmentCLIToolManifest] = []
        for name, tool in sorted(self.environment_cli_tools.items()):
            if not _env_bool_value(_env_tool_value(tool, "enabled", True)):
                continue
            placement = str(_env_tool_value(tool, "placement", "local") or "local")
            if placement == "server":
                continue
            tool_name = str(_env_tool_value(tool, "name", name) or name)
            command = str(_env_tool_value(tool, "command", "") or "")
            check = str(_env_tool_value(tool, "check", "") or "")
            if not tool_name or not command or not check:
                continue
            capabilities = _env_tool_value(tool, "capabilities", [])
            if not isinstance(capabilities, list):
                capabilities = []
            requirements = _env_tool_value(tool, "requirements", {})
            if not isinstance(requirements, dict):
                requirements = {}
            version = _env_tool_value(tool, "version", None)
            tools.append(
                EnvironmentCLIToolManifest(
                    name=tool_name,
                    command=command,
                    placement=placement,
                    capabilities=[str(item) for item in capabilities],
                    requirements={str(k): str(v) for k, v in requirements.items()},
                    check=check,
                    install=str(_env_tool_value(tool, "install", "") or ""),
                    version=str(version) if version is not None else None,
                    source=str(_env_tool_value(tool, "source", "") or ""),
                    description=str(_env_tool_value(tool, "description", "") or ""),
                    repo_url=str(_env_tool_value(tool, "repo_url", "") or ""),
                    docs=_env_docs_value(_env_tool_value(tool, "docs", [])),
                    evidence=_env_string_dict_list_value(
                        _env_tool_value(tool, "evidence", [])
                    ),
                    install_prompt=str(
                        _env_tool_value(tool, "install_prompt", "") or ""
                    ),
                    verify_prompt=str(_env_tool_value(tool, "verify_prompt", "") or ""),
                    notes=_env_string_list_value(_env_tool_value(tool, "notes", [])),
                    credentials=_env_string_list_value(
                        _env_tool_value(tool, "credentials", [])
                    ),
                    risk_level=str(_env_tool_value(tool, "risk_level", "") or ""),
                    last_action=str(_env_tool_value(tool, "last_action", "") or ""),
                    last_updated=str(_env_tool_value(tool, "last_updated", "") or ""),
                )
            )
        mcp_servers: list[EnvironmentMCPServerManifest] = []
        for server in self.mcp_servers:
            if not getattr(server, "enabled", True):
                continue
            if getattr(server, "placement", "server") not in {"peer", "both"}:
                continue
            launch = getattr(server, "launch", None)
            if launch is None:
                command = str(getattr(server, "command", "") or "")
                launch_args = list(getattr(server, "args", []) or [])
                launch_env = dict(getattr(server, "env", {}) or {})
                launch_cwd = getattr(server, "cwd", None)
            else:
                command = str(getattr(launch, "command", "") or "")
                launch_args = list(getattr(launch, "args", []) or [])
                launch_env = dict(getattr(launch, "env", {}) or {})
                launch_cwd = getattr(launch, "cwd", None)
            if not command:
                continue
            mcp_servers.append(
                EnvironmentMCPServerManifest(
                    name=str(getattr(server, "name", "") or ""),
                    command=command,
                    args=[str(arg) for arg in launch_args],
                    env={str(k): str(v) for k, v in launch_env.items()},
                    cwd=str(launch_cwd) if launch_cwd is not None else None,
                    placement=str(getattr(server, "placement", "peer") or "peer"),
                    distribution=str(
                        getattr(server, "distribution", "command") or "command"
                    ),
                    requirements=dict(getattr(server, "requirements", {}) or {}),
                    check=str(getattr(server, "check", "") or ""),
                    install=str(getattr(server, "install", "") or ""),
                    version=(
                        str(getattr(server, "version"))
                        if getattr(server, "version", None) is not None
                        else None
                    ),
                    source=str(getattr(server, "source", "") or ""),
                    description=str(getattr(server, "description", "") or ""),
                    repo_url=str(getattr(server, "repo_url", "") or ""),
                    docs=_env_docs_value(getattr(server, "docs", [])),
                    evidence=_env_string_dict_list_value(
                        getattr(server, "evidence", [])
                    ),
                    install_prompt=str(getattr(server, "install_prompt", "") or ""),
                    verify_prompt=str(getattr(server, "verify_prompt", "") or ""),
                    notes=_env_string_list_value(getattr(server, "notes", [])),
                    credentials=_env_string_list_value(
                        getattr(server, "credentials", [])
                    ),
                    risk_level=str(getattr(server, "risk_level", "") or ""),
                    last_action=str(getattr(server, "last_action", "") or ""),
                    last_updated=str(getattr(server, "last_updated", "") or ""),
                )
            )
        skills: list[EnvironmentSkillManifest] = []
        for name, skill in sorted(self.environment_skills.items()):
            if not _env_bool_value(_env_tool_value(skill, "enabled", True)):
                continue
            skill_name = str(_env_tool_value(skill, "name", name) or name)
            check = str(_env_tool_value(skill, "check", "") or "")
            if not skill_name or not check:
                continue
            version = _env_tool_value(skill, "version", None)
            requirements = _env_tool_value(skill, "requirements", {})
            if not isinstance(requirements, dict):
                requirements = {}
            skills.append(
                EnvironmentSkillManifest(
                    name=skill_name,
                    scope=str(_env_tool_value(skill, "scope", "project") or "project"),
                    check=check,
                    install=str(_env_tool_value(skill, "install", "") or ""),
                    version=str(version) if version is not None else None,
                    source=str(_env_tool_value(skill, "source", "") or ""),
                    description=str(_env_tool_value(skill, "description", "") or ""),
                    path_hint=(
                        str(_env_tool_value(skill, "path_hint"))
                        if _env_tool_value(skill, "path_hint", None) is not None
                        else None
                    ),
                    requirements={str(k): str(v) for k, v in requirements.items()},
                    repo_url=str(_env_tool_value(skill, "repo_url", "") or ""),
                    docs=_env_docs_value(_env_tool_value(skill, "docs", [])),
                    evidence=_env_string_dict_list_value(
                        _env_tool_value(skill, "evidence", [])
                    ),
                    install_prompt=str(
                        _env_tool_value(skill, "install_prompt", "") or ""
                    ),
                    verify_prompt=str(_env_tool_value(skill, "verify_prompt", "") or ""),
                    notes=_env_string_list_value(_env_tool_value(skill, "notes", [])),
                    credentials=_env_string_list_value(
                        _env_tool_value(skill, "credentials", [])
                    ),
                    risk_level=str(_env_tool_value(skill, "risk_level", "") or ""),
                    last_action=str(_env_tool_value(skill, "last_action", "") or ""),
                    last_updated=str(_env_tool_value(skill, "last_updated", "") or ""),
                )
            )
        return EnvironmentManifestResponse(
            cli_tools=tools,
            mcp_servers=mcp_servers,
            skills=skills,
            prompt=self._build_environment_sync_prompt(
                tools, mcp_servers, skills, workspace
            ),
        )

    @staticmethod
    def _build_environment_sync_prompt(
        tools: list[EnvironmentCLIToolManifest],
        mcp_servers: list[EnvironmentMCPServerManifest],
        skills: list[EnvironmentSkillManifest],
        workspace: str,
    ) -> str:
        if not tools and not mcp_servers and not skills:
            return ""
        manifest = json.dumps(
            {
                "cli_tools": [tool.to_dict() for tool in tools],
                "mcp_servers": [server.to_dict() for server in mcp_servers],
                "skills": [skill.to_dict() for skill in skills],
            },
            ensure_ascii=False,
            indent=2,
        )
        workspace_line = workspace or "(peer working directory)"
        return (
            "You are the EZCode lightweight environment sync agent.\n"
            "The server is the authority for the environment manifest below. Work only from "
            "this manifest; do not scan PATH broadly, discover unrelated tools, or "
            "build a persistent inventory database.\n\n"
            f"Peer workspace: {workspace_line}\n\n"
            "Environment manifest:\n"
            f"```json\n{manifest}\n```\n\n"
            "Procedure:\n"
            "1. For each CLI, MCP, and skill entry with a `check` command, use the shell "
            "tool to run exactly that command from the peer workspace.\n"
            "2. Treat successful checks as available. For failures, explain the "
            "missing or mismatched tool and quote the configured `install` command "
            "if one is present.\n"
            "3. Before running any install command, read that entry's `install_prompt`, "
            "`docs`, `evidence`, `credentials`, `risk_level`, and `notes`; present a "
            "concise install plan grounded in those fields and wait for normal user "
            "approval. Do not invent install steps "
            "outside the manifest. Do not install Node, Python, uv, npm, "
            "pipx, or other base runtimes automatically; report them as blockers if "
            "they are missing.\n"
            "4. After any approved install command, follow that entry's `verify_prompt` "
            "when present, then rerun that tool's `check` command.\n"
            "5. If a check still fails because the declared command is not found, run "
            "`command -v <command>` on Unix-like peers or `Get-Command <command>` "
            "on Windows peers, then report the active PATH and the directory that "
            "should be added.\n"
            "6. Do not edit shell profiles, PATH, npm prefix, PowerShell profiles, "
            "or system environment variables unless the user approves that exact "
            "change.\n"
            "7. Treat `docs` as reference links for troubleshooting and reporting. "
            "If a configured guide conflicts with the install command, stop and "
            "report the manifest inconsistency.\n"
            "8. Finish with a compact status table: tool, capability, check result, "
            "action taken, remaining blocker.\n"
        )


def _package_version() -> str:
    try:
        return version("reuleauxcoder")
    except PackageNotFoundError:
        return "0.0.0"


def _parse_bind(bind: str) -> tuple[str, int]:
    host, sep, port = bind.rpartition(":")
    if not sep or not host:
        raise ValueError(f"Invalid relay bind address: {bind!r}")
    return host, int(port)


def _env_tool_value(tool: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(tool, dict):
        return tool.get(field_name, default)
    return getattr(tool, field_name, default)


def _env_bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _env_string_list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def _env_docs_value(value: Any) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    if not isinstance(value, list):
        return docs
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue
        docs.append({"title": title, "url": url})
    return docs


def _env_string_dict_list_value(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = {
            str(key): str(val).strip()
            for key, val in item.items()
            if val is not None and str(val).strip()
        }
        if normalized:
            items.append(normalized)
    return items
