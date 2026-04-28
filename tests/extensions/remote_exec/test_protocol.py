"""Tests for remote execution protocol message models."""

from __future__ import annotations

import pytest

from reuleauxcoder.extensions.remote_exec.protocol import (
    CleanupRequest,
    CleanupResult,
    DisconnectNotice,
    EnvironmentCLIToolManifest,
    EnvironmentMCPServerManifest,
    EnvironmentSkillManifest,
    EnvironmentManifestRequest,
    EnvironmentManifestResponse,
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
    ToolPreviewRequest,
    ToolPreviewResult,
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
        assert restored.servers[0].artifact is not None
        assert restored.servers[0].distribution == "artifact"
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


class TestEnvironmentManifest:
    def test_manifest_roundtrip(self) -> None:
        response = EnvironmentManifestResponse(
            cli_tools=[
                EnvironmentCLIToolManifest(
                    name="gitnexus",
                    command="gitnexus",
                    capabilities=["repo_index"],
                    check="gitnexus --version",
                    install="npm install -g gitnexus",
                    version="latest",
                    source="npm",
                )
            ],
            mcp_servers=[
                EnvironmentMCPServerManifest(
                    name="gitnexus",
                    command="gitnexus",
                    args=["mcp"],
                    placement="peer",
                    distribution="command",
                    check="gitnexus --version",
                    install="npm install -g gitnexus@1.6.3",
                    requirements={"node": ">=20", "npm": "required"},
                )
            ],
            skills=[
                EnvironmentSkillManifest(
                    name="collaborating-with-claude",
                    scope="user",
                    check="Test-Path ~/.agents/skills/collaborating-with-claude/SKILL.md",
                    install="python install-skill.py",
                    version="1.0.0",
                    source="github",
                    description="Claude bridge skill",
                    path_hint="~/.agents/skills/collaborating-with-claude/SKILL.md",
                )
            ],
            prompt="check gitnexus",
        )

        restored = EnvironmentManifestResponse.from_dict(response.to_dict())

        assert restored.cli_tools[0].name == "gitnexus"
        assert restored.cli_tools[0].capabilities == ["repo_index"]
        assert restored.cli_tools[0].check == "gitnexus --version"
        assert restored.cli_tools[0].install == "npm install -g gitnexus"
        assert restored.mcp_servers[0].name == "gitnexus"
        assert restored.mcp_servers[0].args == ["mcp"]
        assert restored.mcp_servers[0].distribution == "command"
        assert restored.mcp_servers[0].requirements["node"] == ">=20"
        assert restored.skills[0].name == "collaborating-with-claude"
        assert restored.skills[0].scope == "user"
        assert restored.skills[0].path_hint == "~/.agents/skills/collaborating-with-claude/SKILL.md"
        assert restored.prompt == "check gitnexus"

    def test_manifest_request_roundtrip(self) -> None:
        req = EnvironmentManifestRequest(
            peer_token="pt_1", os="windows", arch="amd64", workspace="G:/repo"
        )

        restored = EnvironmentManifestRequest.from_dict(req.to_dict())

        assert restored.peer_token == "pt_1"
        assert restored.os == "windows"
        assert restored.arch == "amd64"
        assert restored.workspace == "G:/repo"


class TestExecToolRequest:
    def test_roundtrip(self) -> None:
        req = ExecToolRequest(
            tool_name="shell",
            args={"command": "ls"},
            cwd="/tmp",
            timeout_sec=60,
            expected_state={"old_sha256": "abc"},
        )
        d = req.to_dict()
        restored = ExecToolRequest.from_dict(d)
        assert restored.tool_name == "shell"
        assert restored.args == {"command": "ls"}
        assert restored.cwd == "/tmp"
        assert restored.timeout_sec == 60
        assert restored.expected_state == {"old_sha256": "abc"}

    def test_defaults(self) -> None:
        req = ExecToolRequest(tool_name="read_file")
        assert req.args == {}
        assert req.cwd is None
        assert req.timeout_sec == 30
        assert req.expected_state == {}


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


class TestToolPreview:
    def test_request_roundtrip(self) -> None:
        req = ToolPreviewRequest(
            tool_name="write_file",
            args={"file_path": "a.txt", "content": "hello"},
            cwd="/repo",
            timeout_sec=12,
        )

        restored = ToolPreviewRequest.from_dict(req.to_dict())

        assert restored.tool_name == "write_file"
        assert restored.args["file_path"] == "a.txt"
        assert restored.cwd == "/repo"
        assert restored.timeout_sec == 12

    def test_result_roundtrip(self) -> None:
        result = ToolPreviewResult(
            ok=True,
            sections=[
                {
                    "id": "diff",
                    "kind": "diff",
                    "content": "--- a/a.txt\n+++ b/a.txt\n",
                }
            ],
            resolved_path="/repo/a.txt",
            old_sha256="abc",
            old_exists=True,
            old_size=10,
            old_mtime_ns=123,
            diff="diff",
            original_text="old",
            modified_text="new",
            meta={"mode": "preview"},
        )

        restored = ToolPreviewResult.from_dict(result.to_dict())

        assert restored.ok is True
        assert restored.sections[0]["kind"] == "diff"
        assert restored.resolved_path == "/repo/a.txt"
        assert restored.old_sha256 == "abc"
        assert restored.old_exists is True
        assert restored.old_size == 10
        assert restored.old_mtime_ns == 123
        assert restored.original_text == "old"
        assert restored.modified_text == "new"
        assert restored.meta["mode"] == "preview"


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
