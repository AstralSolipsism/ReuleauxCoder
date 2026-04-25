"""Tests for remote execution protocol message models."""

from __future__ import annotations

import pytest

from reuleauxcoder.extensions.remote_exec.protocol import (
    CleanupRequest,
    CleanupResult,
    DisconnectNotice,
    ErrorMessage,
    ExecToolRequest,
    ExecToolResult,
    Heartbeat,
    MCPArtifactManifest,
    MCPLaunchManifest,
    MCPManifestRequest,
    MCPManifestResponse,
    MCPServerManifest,
    PeerMCPToolsReport,
    RemoteMCPToolInfo,
    RegisterRejected,
    RegisterRequest,
    RegisterResponse,
    RelayEnvelope,
    ToolStreamChunk,
)


class TestRelayEnvelope:
    def test_roundtrip(self) -> None:
        env = RelayEnvelope(
            type="exec_tool",
            request_id="req-123",
            peer_id="peer-456",
            payload={"tool_name": "shell", "args": {"command": "ls"}},
        )
        d = env.to_dict()
        restored = RelayEnvelope.from_dict(d)
        assert restored.type == "exec_tool"
        assert restored.request_id == "req-123"
        assert restored.peer_id == "peer-456"
        assert restored.payload["tool_name"] == "shell"


class TestRegisterRequest:
    def test_roundtrip(self) -> None:
        req = RegisterRequest(
            bootstrap_token="bt_abc",
            cwd="/tmp",
            workspace_root="/workspace",
            capabilities=["shell", "read_file"],
        )
        d = req.to_dict()
        restored = RegisterRequest.from_dict(d)
        assert restored.bootstrap_token == "bt_abc"
        assert restored.cwd == "/tmp"
        assert restored.workspace_root == "/workspace"
        assert restored.capabilities == ["shell", "read_file"]


class TestRegisterResponse:
    def test_roundtrip(self) -> None:
        resp = RegisterResponse(
            peer_id="p1", peer_token="pt_xyz", heartbeat_interval_sec=15
        )
        d = resp.to_dict()
        restored = RegisterResponse.from_dict(d)
        assert restored.peer_id == "p1"
        assert restored.peer_token == "pt_xyz"
        assert restored.heartbeat_interval_sec == 15


class TestRegisterRejected:
    def test_roundtrip(self) -> None:
        rej = RegisterRejected(reason="bad token")
        d = rej.to_dict()
        restored = RegisterRejected.from_dict(d)
        assert restored.reason == "bad token"


class TestHeartbeat:
    def test_roundtrip(self) -> None:
        hb = Heartbeat(peer_token="pt_tok", ts=1234.5)
        d = hb.to_dict()
        restored = Heartbeat.from_dict(d)
        assert restored.peer_token == "pt_tok"
        assert restored.ts == 1234.5


class TestMCPManifest:
    def test_manifest_roundtrip(self) -> None:
        response = MCPManifestResponse(
            servers=[
                MCPServerManifest(
                    name="filesystem",
                    version="1.0.0",
                    artifact=MCPArtifactManifest(
                        platform="linux-amd64",
                        path="filesystem/1.0.0/linux-amd64.tar.gz",
                        sha256="abc",
                        url="/remote/mcp/artifacts/filesystem/1.0.0/linux-amd64.tar.gz",
                    ),
                    launch=MCPLaunchManifest(
                        command="{{bundle}}/filesystem-mcp",
                        args=["--root", "{{workspace}}"],
                        env={"MODE": "local"},
                    ),
                    permissions={"tools": {"write_file": "require_approval"}},
                    requirements={"node": "required", "npm": "required"},
                )
            ],
            diagnostics=[{"server": "missing", "level": "error"}],
        )

        restored = MCPManifestResponse.from_dict(response.to_dict())

        assert restored.servers[0].name == "filesystem"
        assert restored.servers[0].artifact.platform == "linux-amd64"
        assert restored.servers[0].launch.args == ["--root", "{{workspace}}"]
        assert restored.servers[0].requirements["node"] == "required"
        assert restored.diagnostics[0]["server"] == "missing"

    def test_manifest_request_roundtrip(self) -> None:
        req = MCPManifestRequest(
            peer_token="pt_1", os="linux", arch="amd64", workspace="/repo"
        )
        restored = MCPManifestRequest.from_dict(req.to_dict())
        assert restored.peer_token == "pt_1"
        assert restored.os == "linux"
        assert restored.arch == "amd64"
        assert restored.workspace == "/repo"

    def test_tools_report_roundtrip(self) -> None:
        report = PeerMCPToolsReport(
            peer_token="pt_1",
            tools=[
                RemoteMCPToolInfo(
                    name="search",
                    description="Search docs",
                    input_schema={"type": "object"},
                    server_name="docs",
                )
            ],
            diagnostics=[{"level": "warning"}],
        )
        restored = PeerMCPToolsReport.from_dict(report.to_dict())
        assert restored.tools[0].name == "search"
        assert restored.tools[0].server_name == "docs"
        assert restored.diagnostics[0]["level"] == "warning"


class TestExecToolRequest:
    def test_roundtrip(self) -> None:
        req = ExecToolRequest(
            tool_name="shell",
            args={"command": "ls"},
            cwd="/tmp",
            timeout_sec=60,
        )
        d = req.to_dict()
        restored = ExecToolRequest.from_dict(d)
        assert restored.tool_name == "shell"
        assert restored.args == {"command": "ls"}
        assert restored.cwd == "/tmp"
        assert restored.timeout_sec == 60

    def test_defaults(self) -> None:
        req = ExecToolRequest(tool_name="read_file")
        assert req.args == {}
        assert req.cwd is None
        assert req.timeout_sec == 30


class TestExecToolResult:
    def test_roundtrip(self) -> None:
        res = ExecToolResult(
            ok=False,
            result="",
            error_code="PEER_DISCONNECTED",
            error_message="peer gone",
            meta={"exit_code": 1},
        )
        d = res.to_dict()
        restored = ExecToolResult.from_dict(d)
        assert restored.ok is False
        assert restored.error_code == "PEER_DISCONNECTED"
        assert restored.meta["exit_code"] == 1


class TestToolStreamChunk:
    def test_roundtrip(self) -> None:
        chunk = ToolStreamChunk(chunk_type="stdout", data="hello", meta={"seq": 1})
        d = chunk.to_dict()
        restored = ToolStreamChunk.from_dict(d)
        assert restored.chunk_type == "stdout"
        assert restored.data == "hello"


class TestDisconnectNotice:
    def test_roundtrip(self) -> None:
        n = DisconnectNotice(reason="shutdown")
        d = n.to_dict()
        restored = DisconnectNotice.from_dict(d)
        assert restored.reason == "shutdown"

    def test_default_reason(self) -> None:
        n = DisconnectNotice.from_dict({})
        assert n.reason == "peer_initiated"


class TestCleanupRequest:
    def test_roundtrip(self) -> None:
        req = CleanupRequest()
        d = req.to_dict()
        restored = CleanupRequest.from_dict(d)
        assert isinstance(restored, CleanupRequest)


class TestCleanupResult:
    def test_roundtrip(self) -> None:
        res = CleanupResult(ok=True, removed_items=["/tmp/a"], error_message=None)
        d = res.to_dict()
        restored = CleanupResult.from_dict(d)
        assert restored.ok is True
        assert restored.removed_items == ["/tmp/a"]


class TestErrorMessage:
    def test_roundtrip(self) -> None:
        err = ErrorMessage(code="AUTH_FAILED", message="bad token")
        d = err.to_dict()
        restored = ErrorMessage.from_dict(d)
        assert restored.code == "AUTH_FAILED"
        assert restored.message == "bad token"
