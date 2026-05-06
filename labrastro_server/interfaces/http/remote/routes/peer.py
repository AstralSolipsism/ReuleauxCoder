from __future__ import annotations

import gzip
import json
import secrets
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote

from labrastro_server.interfaces.http.remote.bootstrap import (
    generate_bootstrap_script,
    generate_powershell_bootstrap_script,
)
from labrastro_server.interfaces.http.remote.helpers import (
    GZIP_MIN_BYTES,
    optional_payload_str,
    package_version,
    strong_etag,
)
from labrastro_server.interfaces.http.remote.protocol import (
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
    EnvironmentManifestRequest,
    EnvironmentManifestResponse,
    ExecToolResult,
    Heartbeat,
    MCPManifestRequest,
    MCPManifestResponse,
    PeerMCPToolsReport,
    RegisterRejected,
    RegisterRequest,
    RelayEnvelope,
    SessionDeleteRequest,
    SessionListRequest,
    SessionLoadRequest,
    SessionModelSwitchRequest,
    SessionNewRequest,
    SessionSnapshotRequest,
    ToolPreviewResult,
)
from labrastro_server.relay.errors import RegisterRejectedError
from labrastro_server.services.agent_runtime.control_plane import RuntimeTaskRequest
from labrastro_server.services.agent_runtime.executor_backend import (
    ExecutorEvent,
    ExecutorRunResult,
)
from reuleauxcoder.interfaces.events import UIEventKind

class RemotePeerRoutes:
    def _handle_capabilities(self) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "api_version": 1,
                "server_version": package_version(),
                "capabilities": {
                    "sessions": self.service.session_handler is not None,
                    "chat_stream": self.service.stream_chat_handler is not None,
                    "taskflow": self.service.taskflow_service is not None,
                    "issue_assignment": self.service.issue_assignment_service
                    is not None,
                    "fresh_session_without_session_hint": self.service.stream_chat_handler
                    is not None,
                    "peer_token_heartbeat_refresh": True,
                },
            },
        )

    def _handle_bootstrap(self, parsed, script_kind: str) -> None:
        del parsed
        configured_secret = self.service.bootstrap_access_secret
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
        token = self.service.issue_bootstrap_token(
            ttl_sec=self.service.bootstrap_token_ttl_sec
        )
        host_header = self.headers.get("Host")
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "http")
        request_base_url = (
            f"{forwarded_proto}://{host_header}"
            if host_header
            else self.service.base_url
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
            {"Cache-Control": "no-store"},
        )

    def _handle_register(self) -> None:
        payload = self._read_json()
        try:
            resp = self.service.relay_server._on_register(
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
        self.service.ui_bus and self.service.ui_bus.success(
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
        peer_id = self.service.relay_server.token_manager.refresh_peer_token(
            hb.peer_token, ttl_sec=self.service.relay_server.peer_token_ttl_sec
        )
        if peer_id is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
            )
            return
        self.service.relay_server.registry.update_heartbeat(peer_id)
        self._send_json(HTTPStatus.OK, {"ok": True, "peer_id": peer_id})

    def _handle_poll(self) -> None:
        payload = self._read_json()
        peer_token = payload.get("peer_token")
        if not isinstance(peer_token, str) or not peer_token:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "peer_token_required"}
            )
            return
        peer_id = self.service.relay_server.token_manager.verify_peer_token(
            peer_token
        )
        if peer_id is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED, {"error": "invalid_peer_token"}
            )
            return
        self.service.relay_server.registry.update_heartbeat(peer_id)
        env = self.service._next_envelope(peer_id)
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
        peer_id = self.service.relay_server.token_manager.verify_peer_token(
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
        self.service.relay_server.handle_inbound(peer_id, env)
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_disconnect(self) -> None:
        payload = self._read_json()
        peer_token = payload.get("peer_token")
        peer_id = self.service.relay_server.token_manager.verify_peer_token(
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
        self.service._abort_peer_chat_sessions(
            peer_id, f"peer_disconnected: {notice.reason}"
        )
        self.service.relay_server.disconnect_peer(peer_id, notice.reason)
        self.service.ui_bus and self.service.ui_bus.warning(
            f"Remote peer disconnected: {peer_id}",
            kind=UIEventKind.REMOTE,
            peer_id=peer_id,
            reason=notice.reason,
        )
        self._send_json(HTTPStatus.OK, {"ok": True})


