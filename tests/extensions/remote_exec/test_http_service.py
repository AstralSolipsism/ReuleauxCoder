"""Tests for the HTTP transport adapter around the remote relay host."""

from __future__ import annotations

import json
import hashlib
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib import request
from urllib.error import HTTPError

import pytest


_URLOPEN = request.build_opener(request.ProxyHandler({})).open

_GO_AVAILABLE = shutil.which("go") is not None

from reuleauxcoder.extensions.remote_exec.http_service import RemoteRelayHTTPService
from reuleauxcoder.domain.config.models import (
    EnvironmentCLIToolConfig,
    MCPArtifactConfig,
    MCPLaunchConfig,
    MCPServerConfig,
)
from reuleauxcoder.extensions.remote_exec.protocol import (
    ChatResponse,
    ChatStartRequest,
    CleanupResult,
    ExecToolResult,
    SessionListRequest,
    SessionLoadRequest,
    SessionNewRequest,
    SessionSnapshotRequest,
    ToolPreviewRequest,
    ToolPreviewResult,
    RelayEnvelope,
)
from reuleauxcoder.extensions.remote_exec.server import RelayServer
from reuleauxcoder.extensions.tools.builtin.edit import EditFileTool
from reuleauxcoder.extensions.tools.builtin.glob import GlobTool
from reuleauxcoder.extensions.tools.builtin.grep import GrepTool
from reuleauxcoder.extensions.tools.builtin.read import ReadFileTool
from reuleauxcoder.extensions.tools.builtin.shell import ShellTool
from reuleauxcoder.extensions.tools.builtin.write import WriteFileTool
from reuleauxcoder.extensions.remote_exec.backend import RemoteRelayToolBackend
from reuleauxcoder.interfaces.entrypoint.runner import (
    _default_create_remote_artifact_provider,
)
from reuleauxcoder.interfaces.events import UIEventBus


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_request(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=request_headers, method=method)
    with _URLOPEN(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else {}


def _text_request(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    req = request.Request(url, headers=headers or {}, method="GET")
    with _URLOPEN(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def _bytes_request(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = request.Request(url, headers=headers or {}, method="GET")
    with _URLOPEN(req, timeout=5) as resp:
        return resp.status, resp.read()


def _build_go_agent_binary() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    agent_dir = repo_root / "reuleauxcoder-agent"
    target_dir = Path(tempfile.mkdtemp(prefix="rc-go-agent-bin-"))
    binary_path = target_dir / "reuleauxcoder-agent"
    subprocess.run(
        ["go", "build", "-o", str(binary_path), "./cmd/reuleauxcoder-agent"],
        cwd=agent_dir,
        check=True,
        timeout=120,
    )
    return binary_path


def _cleanup_provider_build_dir(provider: object) -> None:
    build_dir = getattr(provider, "_build_dir", None)
    if isinstance(build_dir, Path):
        shutil.rmtree(build_dir, ignore_errors=True)


class TestRemoteRelayHTTPService:
    def test_relay_send_preview_request_roundtrips_result(self) -> None:
        captured: list[RelayEnvelope] = []

        def send_fn(peer_id: str, envelope: RelayEnvelope) -> None:
            captured.append(envelope)
            relay.handle_inbound(
                peer_id,
                RelayEnvelope(
                    type="tool_preview_result",
                    request_id=envelope.request_id,
                    peer_id=peer_id,
                    payload=ToolPreviewResult(
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
                    ).to_dict(),
                ),
            )

        relay = RelayServer(send_fn=send_fn)
        relay.start()
        try:
            peer_id = relay.registry.register(
                {"capabilities": ["tool_preview"], "cwd": "/repo"}
            )
            result = relay.send_preview_request(
                peer_id,
                ToolPreviewRequest(
                    tool_name="write_file",
                    args={"file_path": "a.txt", "content": "new"},
                    cwd="/repo",
                ),
                timeout_sec=2,
            )

            assert captured[0].type == "preview_tool"
            assert result.ok is True
            assert result.sections[0]["kind"] == "diff"
            assert result.resolved_path == "/repo/a.txt"
            assert result.old_sha256 == "abc"
            assert result.old_exists is True
        finally:
            relay.stop()

    def test_admin_provider_and_model_endpoints_require_secret_and_mask_keys(
        self, tmp_path: Path
    ) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        reloads: list[str] = []
        config_path = tmp_path / "config.yaml"
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            admin_access_secret="admin-secret",
            admin_config_path=config_path,
            admin_config_reload_handler=lambda: reloads.append("reload"),
            admin_provider_test_handler=lambda provider, model, prompt: {
                "ok": True,
                "provider_id": provider.id,
                "model": model,
                "prompt": prompt,
            },
            admin_provider_models_handler=lambda provider: {
                "ok": True,
                "provider_id": provider.id,
                "unsupported": False,
                "models": [
                    {"id": "deepseek-chat", "owned_by": "deepseek"},
                    {"id": "deepseek-reasoner", "owned_by": "deepseek"},
                ],
            },
        )
        service.start()
        try:
            try:
                _json_request(
                    "POST", f"{service.base_url}/remote/admin/providers/list", {}
                )
                raise AssertionError("admin endpoint should require a secret")
            except HTTPError as exc:
                assert exc.code == 403

            admin_headers = {"X-RC-Admin-Secret": "admin-secret"}
            status, record = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/record",
                {
                    "provider_id": "deepseek",
                    "type": "openai_chat",
                    "compat": "deepseek",
                    "api_key": "sk-secret-value",
                    "base_url": "https://api.deepseek.com",
                },
                headers=admin_headers,
            )
            assert status == 200
            assert record["ok"] is True
            assert record["provider"]["api_key_hint"] == "sk-s...alue"
            assert "api_key" not in record["provider"]

            _, update = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/record",
                {
                    "provider_id": "deepseek",
                    "type": "openai_chat",
                    "compat": "deepseek",
                    "base_url": "https://api.deepseek.com/v1",
                },
                headers=admin_headers,
            )
            assert update["created"] is False

            _, providers = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/list",
                {},
                headers=admin_headers,
            )
            assert providers["providers"][0]["api_key_hint"] == "sk-s...alue"
            assert "api_key" not in providers["providers"][0]
            assert providers["providers"][0]["enabled"] is True

            _, model_list = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/models",
                {"provider_id": "deepseek"},
                headers=admin_headers,
            )
            assert model_list["models"][0]["id"] == "deepseek-chat"

            _, test_result = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/test",
                {"provider_id": "deepseek", "model": "deepseek-chat", "prompt": "ping"},
                headers=admin_headers,
            )
            assert test_result == {
                "ok": True,
                "provider_id": "deepseek",
                "model": "deepseek-chat",
                "prompt": "ping",
            }

            _, profile = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/models/record",
                {
                    "profile_id": "deepseek-main",
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "max_tokens": 4096,
                    "max_context_tokens": 128000,
                    "temperature": 0,
                    "thinking_enabled": True,
                },
                headers=admin_headers,
            )
            assert profile["model_profile"]["provider"] == "deepseek"
            assert "api_key" not in profile["model_profile"]

            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/admin/providers/delete",
                    {"provider_id": "deepseek"},
                    headers=admin_headers,
                )
                raise AssertionError("delete should be blocked while profiles reference provider")
            except HTTPError as exc:
                assert exc.code == 409
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "provider_in_use"
                assert body["blockers"][0]["profile_id"] == "deepseek-main"

            _, active = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/models/activate",
                {"profile_id": "deepseek-main", "target": "both"},
                headers=admin_headers,
            )
            assert active["active_main"] == "deepseek-main"
            assert active["active_sub"] == "deepseek-main"

            _, disabled = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/enable",
                {"provider_id": "deepseek", "enabled": False},
                headers=admin_headers,
            )
            assert disabled["provider"]["enabled"] is False
            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/admin/models/activate",
                    {"profile_id": "deepseek-main", "target": "main"},
                    headers=admin_headers,
                )
                raise AssertionError("disabled provider should block activation")
            except HTTPError as exc:
                assert exc.code == 409
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "provider_disabled"

            _, copied = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/copy",
                {"provider_id": "deepseek", "target_id": "deepseek-copy"},
                headers=admin_headers,
            )
            assert copied["provider"]["id"] == "deepseek-copy"
            assert copied["provider"]["enabled"] is True
            assert copied["provider"]["api_key_hint"] == "sk-s...alue"

            _, models = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/models/list",
                {},
                headers=admin_headers,
            )
            assert models["active_main"] == "deepseek-main"
            assert models["model_profiles"][0]["id"] == "deepseek-main"
            _, deleted = _json_request(
                "POST",
                f"{service.base_url}/remote/admin/providers/delete",
                {"provider_id": "deepseek-copy"},
                headers=admin_headers,
            )
            assert deleted == {"ok": True, "provider_id": "deepseek-copy"}
            assert len(reloads) == 7
            raw = config_path.read_text(encoding="utf-8")
            assert "sk-secret-value" in raw
            assert "active_main: deepseek-main" in raw
            assert "deepseek-copy" not in raw
        finally:
            service.stop()
            relay.stop()

    def test_admin_write_rolls_back_when_reload_fails(
        self, tmp_path: Path
    ) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "providers:\n"
            "  items:\n"
            "    existing:\n"
            "      type: openai_chat\n"
            "      api_key: sk-existing\n"
            "      base_url: https://example.invalid/v1\n",
            encoding="utf-8",
        )
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            admin_access_secret="admin-secret",
            admin_config_path=config_path,
            admin_config_reload_handler=lambda: (_ for _ in ()).throw(
                RuntimeError("reload failed")
            ),
        )
        service.start()
        try:
            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/admin/providers/record",
                    {
                        "provider_id": "broken",
                        "type": "openai_chat",
                        "api_key": "sk-broken",
                        "base_url": "https://broken.invalid/v1",
                    },
                    headers={"X-RC-Admin-Secret": "admin-secret"},
                )
                raise AssertionError("reload failure should surface as HTTP 500")
            except HTTPError as exc:
                assert exc.code == 500
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "config_reload_failed"
            raw = config_path.read_text(encoding="utf-8")
            assert "existing" in raw
            assert "sk-existing" in raw
            assert "broken" not in raw
            assert "sk-broken" not in raw
        finally:
            service.stop()
            relay.stop()

    def test_bootstrap_and_artifact_endpoints(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            artifact_provider=lambda os_name, arch, name: (
                (
                    b"peer-binary",
                    "application/octet-stream",
                )
                if (os_name, arch, name) == ("linux", "amd64", "rcoder-peer")
                else None
            ),
            bootstrap_access_secret="top-secret",
            bootstrap_token_ttl_sec=60,
        )
        service.start()
        try:
            try:
                _text_request(f"{service.base_url}/remote/bootstrap.sh")
                raise AssertionError("bootstrap should require secret")
            except HTTPError as exc:
                assert exc.code == 403
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "invalid_bootstrap_secret"

            status, script = _text_request(
                f"{service.base_url}/remote/bootstrap.sh",
                headers={"X-RC-Bootstrap-Secret": "top-secret"},
            )
            assert status == 200
            assert "rcoder-peer" in script
            assert service.base_url in script
            assert "/remote/artifacts/{os}/{arch}/rcoder-peer" in script
            assert "( : </dev/tty ) 2>/dev/null" in script
            assert "[ -r /dev/tty ]" not in script
            assert 'exec "$BIN" --host "$HOST" --bootstrap-token "$TOKEN"\n' in script

            with _URLOPEN(
                f"{service.base_url}/remote/artifacts/linux/amd64/rcoder-peer",
                timeout=5,
            ) as resp:
                assert resp.status == 200
                assert resp.read() == b"peer-binary"
        finally:
            service.stop()
            relay.stop()

    def test_peer_mcp_manifest_artifact_and_tools_report(self, tmp_path: Path) -> None:
        artifact_root = tmp_path / "artifacts"
        artifact_path = artifact_root / "local-filesystem" / "1.0.0" / "linux-amd64.tar.gz"
        artifact_path.parent.mkdir(parents=True)
        artifact_content = b"fake-archive"
        artifact_path.write_bytes(artifact_content)
        artifact_sha = hashlib.sha256(artifact_content).hexdigest()

        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            mcp_artifact_root=artifact_root,
            mcp_servers=[
                MCPServerConfig(
                    name="github",
                    command="github-mcp",
                    placement="server",
                ),
                MCPServerConfig(
                    name="local-filesystem",
                    command="",
                    placement="peer",
                    distribution="artifact",
                    version="1.0.0",
                    launch=MCPLaunchConfig(
                        command="{{bundle}}/filesystem-mcp",
                        args=["--root", "{{workspace}}"],
                    ),
                    artifacts={
                        "linux-amd64": MCPArtifactConfig(
                            path="local-filesystem/1.0.0/linux-amd64.tar.gz",
                            sha256=artifact_sha,
                            launch=MCPLaunchConfig(
                                command="{{bundle}}/run.sh",
                                args=["--root", "{{workspace}}"],
                            ),
                        )
                    },
                    requirements={"node": "required", "npm": "required"},
                    permissions={"tools": {"write_file": "require_approval"}},
                ),
                MCPServerConfig(
                    name="missing-platform",
                    command="missing",
                    placement="peer",
                    distribution="artifact",
                    version="1.0.0",
                    launch=MCPLaunchConfig(command="{{bundle}}/missing"),
                    artifacts={},
                ),
                MCPServerConfig(
                    name="shared-browser",
                    command="npx",
                    args=["-y", "@demo/browser@1.0.0"],
                    placement="both",
                    distribution="artifact",
                    version="1.0.0",
                    launch=MCPLaunchConfig(command="{{bundle}}/browser"),
                    artifacts={
                        "linux-amd64": MCPArtifactConfig(
                            path="shared-browser/1.0.0/linux-amd64.tar.gz",
                            sha256=artifact_sha,
                        )
                    },
                ),
            ],
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_id = register_body["payload"]["peer_id"]
            peer_token = register_body["payload"]["peer_token"]

            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/mcp/manifest",
                    {"peer_token": "bad", "os": "linux", "arch": "amd64"},
                )
                raise AssertionError("manifest should require valid peer token")
            except HTTPError as exc:
                assert exc.code == 401

            status, manifest = _json_request(
                "POST",
                f"{service.base_url}/remote/mcp/manifest",
                {
                    "peer_token": peer_token,
                    "os": "linux",
                    "arch": "amd64",
                    "workspace": "/tmp/peer",
                },
            )
            assert status == 200
            assert [server["name"] for server in manifest["servers"]] == [
                "local-filesystem",
                "shared-browser",
            ]
            server_manifest = manifest["servers"][0]
            assert server_manifest["artifact"]["sha256"] == artifact_sha
            assert server_manifest["distribution"] == "artifact"
            assert server_manifest["launch"]["command"] == "{{bundle}}/run.sh"
            assert server_manifest["launch"]["args"] == ["--root", "{{workspace}}"]
            assert server_manifest["requirements"] == {
                "node": "required",
                "npm": "required",
            }
            assert server_manifest["permissions"]["tools"]["write_file"] == "require_approval"
            assert manifest["diagnostics"][0]["server"] == "missing-platform"

            try:
                _bytes_request(
                    f"{service.base_url}{server_manifest['artifact']['url']}"
                )
                raise AssertionError("artifact should require peer token")
            except HTTPError as exc:
                assert exc.code == 401

            status, body = _bytes_request(
                f"{service.base_url}{server_manifest['artifact']['url']}",
                headers={"X-RC-Peer-Token": peer_token},
            )
            assert status == 200
            assert body == artifact_content

            status, report = _json_request(
                "POST",
                f"{service.base_url}/remote/mcp/tools",
                {
                    "peer_token": peer_token,
                    "tools": [
                        {
                            "name": "read_file",
                            "description": "Read a local file",
                            "input_schema": {"type": "object"},
                            "server_name": "local-filesystem",
                        }
                    ],
                },
            )
            assert status == 200
            assert report["ok"] is True
            assert relay.get_peer_mcp_tools(peer_id)[0].server_name == "local-filesystem"
        finally:
            service.stop()
            relay.stop()

    def test_peer_mcp_manifest_command_distribution_without_artifact(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            mcp_servers=[
                MCPServerConfig(
                    name="gitnexus",
                    command="gitnexus",
                    args=["mcp"],
                    placement="peer",
                    distribution="command",
                    version="1.6.3",
                    check="gitnexus --version",
                    install="npm install -g gitnexus@1.6.3",
                    requirements={"node": ">=20", "npm": "required"},
                    artifacts={
                        "linux-amd64": MCPArtifactConfig(
                            path="gitnexus/1.6.3/linux-amd64.tar.gz",
                            sha256="legacy",
                        )
                    },
                )
            ],
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_token = register_body["payload"]["peer_token"]

            status, manifest = _json_request(
                "POST",
                f"{service.base_url}/remote/mcp/manifest",
                {
                    "peer_token": peer_token,
                    "os": "linux",
                    "arch": "amd64",
                    "workspace": "/tmp/peer",
                },
            )

            assert status == 200
            assert manifest["diagnostics"] == []
            server = manifest["servers"][0]
            assert server["name"] == "gitnexus"
            assert server["distribution"] == "command"
            assert server["artifact"] is None
            assert server["launch"]["command"] == "gitnexus"
            assert server["launch"]["args"] == ["mcp"]
            assert server["requirements"]["node"] == ">=20"
        finally:
            service.stop()
            relay.stop()

    def test_environment_manifest_endpoint_returns_cli_prompt(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            mcp_servers=[
                MCPServerConfig(
                    name="gitnexus-mcp",
                    command="gitnexus",
                    args=["mcp"],
                    placement="peer",
                    distribution="command",
                    check="gitnexus --version",
                    install="npm install -g gitnexus@1.6.3",
                    requirements={"node": ">=20"},
                )
            ],
            environment_cli_tools={
                "beads": {
                    "command": "beads",
                    "capabilities": ["issue_tracking"],
                    "check": "beads --version",
                    "install": "npm install -g beads",
                    "source": "npm",
                },
                "gitnexus": EnvironmentCLIToolConfig(
                    name="gitnexus",
                    command="gitnexus",
                    capabilities=["repo_index"],
                    check="gitnexus --version",
                    install="npm install -g gitnexus",
                    source="npm",
                )
            },
            environment_skills={
                "collaborating-with-claude": {
                    "scope": "user",
                    "check": "Test-Path ~/.agents/skills/collaborating-with-claude/SKILL.md",
                    "install": "python install-skill.py",
                    "version": "1.0.0",
                    "source": "github",
                    "description": "Claude bridge skill",
                    "path_hint": "~/.agents/skills/collaborating-with-claude/SKILL.md",
                }
            },
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_token = register_body["payload"]["peer_token"]

            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/environment/manifest",
                    {"peer_token": "bad", "os": "linux", "arch": "amd64"},
                )
                raise AssertionError("environment manifest should require valid token")
            except HTTPError as exc:
                assert exc.code == 401

            status, manifest = _json_request(
                "POST",
                f"{service.base_url}/remote/environment/manifest",
                {
                    "peer_token": peer_token,
                    "os": "linux",
                    "arch": "amd64",
                    "workspace": "/tmp/peer",
                },
            )

            assert status == 200
            tools = {tool["name"]: tool for tool in manifest["cli_tools"]}
            mcp_servers = {server["name"]: server for server in manifest["mcp_servers"]}
            skills = {skill["name"]: skill for skill in manifest["skills"]}
            assert tools["gitnexus"]["check"] == "gitnexus --version"
            assert tools["beads"]["capabilities"] == ["issue_tracking"]
            assert mcp_servers["gitnexus-mcp"]["distribution"] == "command"
            assert mcp_servers["gitnexus-mcp"]["requirements"]["node"] == ">=20"
            assert skills["collaborating-with-claude"]["scope"] == "user"
            assert (
                skills["collaborating-with-claude"]["path_hint"]
                == "~/.agents/skills/collaborating-with-claude/SKILL.md"
            )
            assert "do not scan PATH broadly" in manifest["prompt"]
            assert "npm install -g gitnexus" in manifest["prompt"]
            assert "mcp_servers" in manifest["prompt"]
            assert "skills" in manifest["prompt"]
            assert "CLI, MCP, and skill entry" in manifest["prompt"]
            assert "command -v <command>" in manifest["prompt"]
            assert "Get-Command <command>" in manifest["prompt"]
            assert "active PATH" in manifest["prompt"]
            assert "unless the user approves that exact change" in manifest["prompt"]
        finally:
            service.stop()
            relay.stop()

    def test_powershell_bootstrap_endpoint(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            artifact_provider=lambda _os_name, _arch, _name: (
                b"peer-binary",
                "application/octet-stream",
            ),
            bootstrap_access_secret="top-secret",
            bootstrap_token_ttl_sec=60,
        )
        service.start()
        try:
            try:
                _text_request(f"{service.base_url}/remote/bootstrap.ps1")
                raise AssertionError("bootstrap should require secret")
            except HTTPError as exc:
                assert exc.code == 403
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "invalid_bootstrap_secret"

            try:
                _text_request(
                    f"{service.base_url}/remote/bootstrap.ps1",
                    headers={"X-RC-Bootstrap-Secret": "wrong"},
                )
                raise AssertionError("bootstrap should reject wrong secret")
            except HTTPError as exc:
                assert exc.code == 403
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "invalid_bootstrap_secret"

            status, script = _text_request(
                f"{service.base_url}/remote/bootstrap.ps1",
                headers={"X-RC-Bootstrap-Secret": "top-secret"},
            )
            assert status == 200
            assert "$ErrorActionPreference" in script
            assert "bt_" in script
            assert "rcoder-peer.exe" in script
            assert "/remote/artifacts/{os}/{arch}/rcoder-peer" in script
            assert '.Replace("{os}", "windows")' in script
            assert "--interactive" in script
        finally:
            service.stop()
            relay.stop()

    def test_register_poll_result_disconnect_and_cleanup(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(relay_server=relay, bind=f"127.0.0.1:{port}")
        service.start()
        try:
            bootstrap_token = relay.issue_bootstrap_token(ttl_sec=60)
            status, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": bootstrap_token,
                    "cwd": "/tmp/peer",
                    "workspace_root": "/tmp",
                    "capabilities": ["shell", "read_file"],
                },
            )
            assert status == 200
            assert register_body["type"] == "register_ok"
            payload = register_body["payload"]
            peer_id = payload["peer_id"]
            peer_token = payload["peer_token"]

            status, heartbeat_body = _json_request(
                "POST",
                f"{service.base_url}/remote/heartbeat",
                {"peer_token": peer_token, "ts": time.time()},
            )
            assert status == 200
            assert heartbeat_body["peer_id"] == peer_id

            status, poll_body = _json_request(
                "POST",
                f"{service.base_url}/remote/poll",
                {"peer_token": peer_token},
            )
            assert status == 200
            assert poll_body["type"] == "noop"

            result_holder: dict[str, object] = {}

            def run_exec() -> None:
                result_holder["result"] = relay.send_exec_request(
                    peer_id,
                    request=__import__(
                        "reuleauxcoder.extensions.remote_exec.protocol",
                        fromlist=["ExecToolRequest"],
                    ).ExecToolRequest(tool_name="shell", args={"command": "echo hi"}),
                    timeout_sec=2,
                )

            exec_thread = threading.Thread(target=run_exec)
            exec_thread.start()
            time.sleep(0.1)

            status, poll_body = _json_request(
                "POST",
                f"{service.base_url}/remote/poll",
                {"peer_token": peer_token},
            )
            assert status == 200
            assert poll_body["type"] == "exec_tool"
            assert poll_body["payload"]["tool_name"] == "shell"
            req_id = poll_body["request_id"]

            status, result_body = _json_request(
                "POST",
                f"{service.base_url}/remote/result",
                {
                    "peer_token": peer_token,
                    "request_id": req_id,
                    "type": "tool_result",
                    "payload": ExecToolResult(
                        ok=True, result="hello from peer"
                    ).to_dict(),
                },
            )
            assert status == 200
            assert result_body["ok"] is True
            exec_thread.join(timeout=2)
            assert result_holder["result"].result == "hello from peer"

            cleanup_holder: dict[str, object] = {}

            def run_cleanup() -> None:
                cleanup_holder["result"] = relay.request_cleanup(peer_id, timeout_sec=2)

            cleanup_thread = threading.Thread(target=run_cleanup)
            cleanup_thread.start()
            time.sleep(0.1)

            status, poll_body = _json_request(
                "POST",
                f"{service.base_url}/remote/poll",
                {"peer_token": peer_token},
            )
            assert status == 200
            assert poll_body["type"] == "cleanup"
            cleanup_req_id = poll_body["request_id"]

            status, cleanup_body = _json_request(
                "POST",
                f"{service.base_url}/remote/result",
                {
                    "peer_token": peer_token,
                    "request_id": cleanup_req_id,
                    "type": "cleanup_result",
                    "payload": CleanupResult(
                        ok=True, removed_items=["/tmp/rc-peer"]
                    ).to_dict(),
                },
            )
            assert status == 200
            assert cleanup_body["ok"] is True
            cleanup_thread.join(timeout=2)
            assert cleanup_holder["result"].ok is True
            assert cleanup_holder["result"].removed_items == ["/tmp/rc-peer"]

            status, disconnect_body = _json_request(
                "POST",
                f"{service.base_url}/remote/disconnect",
                {"peer_token": peer_token, "reason": "peer_initiated"},
            )
            assert status == 200
            assert disconnect_body["ok"] is True
            assert relay.registry.get(peer_id) is None
        finally:
            service.stop()
            relay.stop()

    def test_all_remote_builtin_tools_dispatch_over_http_contract(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(relay_server=relay, bind=f"127.0.0.1:{port}")
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_id = register_body["payload"]["peer_id"]
            peer_token = register_body["payload"]["peer_token"]

            backend = RemoteRelayToolBackend(relay_server=relay)
            backend.context.peer_id = peer_id
            cases = [
                (
                    ShellTool(backend=backend),
                    {"command": "echo hello"},
                    "shell",
                    "shell-ok",
                ),
                (
                    ReadFileTool(backend=backend),
                    {"file_path": "/tmp/demo.txt"},
                    "read_file",
                    "read-ok",
                ),
                (
                    WriteFileTool(backend=backend),
                    {"file_path": "/tmp/demo.txt", "content": "hello"},
                    "write_file",
                    "write-ok",
                ),
                (
                    EditFileTool(backend=backend),
                    {
                        "file_path": "/tmp/demo.txt",
                        "old_string": "a",
                        "new_string": "b",
                    },
                    "edit_file",
                    "edit-ok",
                ),
                (
                    GlobTool(backend=backend),
                    {"pattern": "*.py", "path": "/tmp"},
                    "glob",
                    "glob-ok",
                ),
                (
                    GrepTool(backend=backend),
                    {"pattern": "hello", "path": "/tmp"},
                    "grep",
                    "grep-ok",
                ),
            ]

            for tool, kwargs, expected_name, expected_result in cases:
                holder: dict[str, object] = {}

                def run_tool(current_tool=tool, current_kwargs=kwargs) -> None:
                    holder["result"] = current_tool.execute(**current_kwargs)

                t = threading.Thread(target=run_tool)
                t.start()
                time.sleep(0.1)

                status, poll_body = _json_request(
                    "POST",
                    f"{service.base_url}/remote/poll",
                    {"peer_token": peer_token},
                )
                assert status == 200
                assert poll_body["type"] == "exec_tool"
                assert poll_body["payload"]["tool_name"] == expected_name
                for key, value in kwargs.items():
                    assert poll_body["payload"]["args"][key] == value

                status, result_body = _json_request(
                    "POST",
                    f"{service.base_url}/remote/result",
                    {
                        "peer_token": peer_token,
                        "request_id": poll_body["request_id"],
                        "type": "tool_result",
                        "payload": ExecToolResult(
                            ok=True, result=expected_result
                        ).to_dict(),
                    },
                )
                assert status == 200
                assert result_body["ok"] is True

                t.join(timeout=2)
                assert holder["result"] == expected_result
        finally:
            service.stop()
            relay.stop()

    def test_register_rejected_over_http(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(relay_server=relay, bind=f"127.0.0.1:{port}")
        service.start()
        try:
            req = request.Request(
                f"{service.base_url}/remote/register",
                data=json.dumps(
                    {"bootstrap_token": "bt_invalid", "cwd": "/tmp"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                _URLOPEN(req, timeout=5)
                assert False, "expected HTTPError"
            except HTTPError as exc:
                assert exc.code == 403
                body = json.loads(exc.read().decode("utf-8"))
                assert body["type"] == "register_rejected"
        finally:
            service.stop()
            relay.stop()

    def test_chat_endpoint_routes_to_host_chat_handler(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            chat_handler=lambda peer_id, prompt: ChatResponse(
                response=f"{peer_id}:{prompt}"
            ),
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_id = register_body["payload"]["peer_id"]
            peer_token = register_body["payload"]["peer_token"]

            status, chat_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat",
                {
                    "peer_token": peer_token,
                    "prompt": "hello",
                },
            )
            assert status == 200
            assert chat_body["response"] == f"{peer_id}:hello"
            assert chat_body.get("error") in (None, "")
        finally:
            service.stop()
            relay.stop()

    def test_chat_endpoint_allows_concurrent_requests_across_peers(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()

        def chat_handler(peer_id: str, prompt: str) -> ChatResponse:
            time.sleep(0.3)
            return ChatResponse(response=f"{peer_id}:{prompt}")

        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            chat_handler=chat_handler,
        )
        service.start()
        try:
            _, register_a = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/a",
                },
            )
            _, register_b = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/b",
                },
            )

            token_a = register_a["payload"]["peer_token"]
            token_b = register_b["payload"]["peer_token"]
            results: dict[str, dict] = {}

            def run_chat(label: str, token: str) -> None:
                _, body = _json_request(
                    "POST",
                    f"{service.base_url}/remote/chat",
                    {"peer_token": token, "prompt": label},
                )
                results[label] = body

            started = time.time()
            t1 = threading.Thread(target=run_chat, args=("p1", token_a))
            t2 = threading.Thread(target=run_chat, args=("p2", token_b))
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)
            elapsed = time.time() - started

            assert "p1" in results and "p2" in results
            assert elapsed < 0.55
        finally:
            service.stop()
            relay.stop()

    def test_disconnect_aborts_active_stream_chat_session(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()

        def stream_chat_handler(_peer_id: str, _prompt: str, session) -> None:
            # Wait long enough so test can force disconnect first.
            session.wait_approval("hold", timeout_sec=2)

        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            stream_chat_handler=stream_chat_handler,
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_token = register_body["payload"]["peer_token"]

            _, start_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat/start",
                {
                    "peer_token": peer_token,
                    "prompt": "long-run",
                },
            )
            chat_id = start_body["chat_id"]

            status, _ = _json_request(
                "POST",
                f"{service.base_url}/remote/disconnect",
                {"peer_token": peer_token, "reason": "test_disconnect"},
            )
            assert status == 200

            _, stream_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat/stream",
                {
                    "peer_token": peer_token,
                    "chat_id": chat_id,
                    "cursor": 0,
                    "timeout_sec": 1,
                },
            )
            assert stream_body["done"] is True
            event_types = [event["type"] for event in stream_body["events"]]
            assert "chat_start" in event_types
            assert "error" in event_types
        finally:
            service.stop()
            relay.stop()

    def test_approval_reply_routes_to_matching_chat_session_only(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()

        def stream_chat_handler(_peer_id: str, _prompt: str, session) -> None:
            approval_id = "approval-1"
            session.register_approval(approval_id)
            session.append_event(
                "approval_request",
                {
                    "approval_id": approval_id,
                    "tool_name": "shell",
                    "tool_source": "builtin",
                    "reason": "need approval",
                },
            )
            decision, reason = session.wait_approval(approval_id, timeout_sec=2)
            session.append_event(
                "approval_resolved",
                {"approval_id": approval_id, "decision": decision, "reason": reason},
            )

        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            stream_chat_handler=stream_chat_handler,
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_token = register_body["payload"]["peer_token"]

            _, start_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat/start",
                {"peer_token": peer_token, "prompt": "approve me"},
            )
            chat_id = start_body["chat_id"]

            _, stream_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat/stream",
                {
                    "peer_token": peer_token,
                    "chat_id": chat_id,
                    "cursor": 0,
                    "timeout_sec": 1,
                },
            )
            approval_events = [
                event
                for event in stream_body["events"]
                if event["type"] == "approval_request"
            ]
            assert approval_events
            approval_id = approval_events[0]["payload"]["approval_id"]

            status, reply_body = _json_request(
                "POST",
                f"{service.base_url}/remote/approval/reply",
                {
                    "peer_token": peer_token,
                    "chat_id": chat_id,
                    "approval_id": approval_id,
                    "decision": "allow_once",
                    "reason": "ok",
                },
            )
            assert status == 200
            assert reply_body["ok"] is True

            _, resolved_body = _json_request(
                "POST",
                f"{service.base_url}/remote/chat/stream",
                {
                    "peer_token": peer_token,
                    "chat_id": chat_id,
                    "cursor": stream_body["next_cursor"],
                    "timeout_sec": 1,
                },
            )
            resolved_events = [
                event
                for event in resolved_body["events"]
                if event["type"] == "approval_resolved"
            ]
            assert resolved_events
            assert resolved_events[0]["payload"]["decision"] == "allow_once"
            assert resolved_body["done"] is True

            bad_chat_req = request.Request(
                f"{service.base_url}/remote/approval/reply",
                data=json.dumps(
                    {
                        "peer_token": peer_token,
                        "chat_id": "missing-chat",
                        "approval_id": approval_id,
                        "decision": "allow_once",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                _URLOPEN(bad_chat_req, timeout=5)
                assert False, "expected HTTPError"
            except HTTPError as exc:
                assert exc.code == 404
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "chat_not_found"

            bad_approval_req = request.Request(
                f"{service.base_url}/remote/approval/reply",
                data=json.dumps(
                    {
                        "peer_token": peer_token,
                        "chat_id": chat_id,
                        "approval_id": "missing-approval",
                        "decision": "allow_once",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                _URLOPEN(bad_approval_req, timeout=5)
                assert False, "expected HTTPError"
            except HTTPError as exc:
                assert exc.code == 404
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "approval_not_found"
        finally:
            service.stop()
            relay.stop()

    def test_session_protocol_models_roundtrip(self) -> None:
        assert ChatStartRequest.from_dict(
            {
                "peer_token": "peer-token",
                "prompt": "hello",
                "session_hint": "session-1",
            }
        ).to_dict() == {
            "peer_token": "peer-token",
            "prompt": "hello",
            "session_hint": "session-1",
        }
        assert SessionListRequest.from_dict(
            {"peer_token": "peer-token", "limit": 5}
        ).to_dict() == {"peer_token": "peer-token", "limit": 5}
        assert SessionLoadRequest.from_dict(
            {"peer_token": "peer-token", "session_id": "session-1"}
        ).to_dict() == {"peer_token": "peer-token", "session_id": "session-1"}
        assert SessionNewRequest.from_dict(
            {"peer_token": "peer-token"}
        ).to_dict() == {"peer_token": "peer-token"}
        assert SessionSnapshotRequest.from_dict(
            {
                "peer_token": "peer-token",
                "session_id": "session-1",
                "snapshot": {"version": 1},
            }
        ).to_dict() == {
            "peer_token": "peer-token",
            "session_id": "session-1",
            "snapshot": {"version": 1},
        }

    def test_sessions_routes_verify_peer_token_and_dispatch(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        calls: list[tuple[str, str, dict]] = []

        def session_handler(action: str, peer_id: str, payload: dict) -> dict:
            calls.append((action, peer_id, payload))
            if action == "load" and payload.get("session_id") == "missing":
                return {"ok": False, "error": "session_not_found", "_status": 404}
            return {"ok": True, "action": action}

        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            session_handler=session_handler,
        )
        service.start()
        try:
            _, register_body = _json_request(
                "POST",
                f"{service.base_url}/remote/register",
                {
                    "bootstrap_token": relay.issue_bootstrap_token(ttl_sec=60),
                    "cwd": "/tmp/peer",
                },
            )
            peer_token = register_body["payload"]["peer_token"]

            status, list_body = _json_request(
                "POST",
                f"{service.base_url}/remote/sessions/list",
                {"peer_token": peer_token},
            )
            assert status == 200
            assert list_body["ok"] is True
            assert list_body["action"] == "list"
            assert calls[-1][0] == "list"

            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/sessions/list",
                    {"peer_token": "bad-token"},
                )
                raise AssertionError("expected invalid token to fail")
            except HTTPError as exc:
                assert exc.code == 401

            try:
                _json_request(
                    "POST",
                    f"{service.base_url}/remote/sessions/load",
                    {"peer_token": peer_token, "session_id": "missing"},
                )
                raise AssertionError("expected missing session to fail")
            except HTTPError as exc:
                assert exc.code == 404
        finally:
            service.stop()
            relay.stop()

    def test_default_artifact_provider_prefers_prebuilt_binary(
        self, tmp_path: Path
    ) -> None:
        provider = _default_create_remote_artifact_provider(UIEventBus())
        artifact_root = getattr(provider, "_artifact_root")
        prebuilt_path = artifact_root / "linux" / "amd64" / "rcoder-peer"
        prebuilt_path.parent.mkdir(parents=True, exist_ok=True)
        prebuilt_path.write_bytes(b"prebuilt-peer")
        try:
            with patch(
                "reuleauxcoder.interfaces.entrypoint.dependencies.subprocess.run"
            ) as mock_run:
                content, content_type = provider("linux", "amd64", "rcoder-peer") or (
                    None,
                    None,
                )
            assert content == b"prebuilt-peer"
            assert content_type == "application/octet-stream"
            mock_run.assert_not_called()
        finally:
            _cleanup_provider_build_dir(provider)
            prebuilt_path.unlink(missing_ok=True)
            for parent in [
                prebuilt_path.parent,
                prebuilt_path.parent.parent,
                artifact_root,
            ]:
                try:
                    parent.rmdir()
                except OSError:
                    pass

    def test_default_artifact_provider_raises_without_prebuilt_or_go(self) -> None:
        provider = _default_create_remote_artifact_provider(UIEventBus())
        try:
            with patch(
                "reuleauxcoder.interfaces.entrypoint.dependencies.shutil.which",
                return_value=None,
            ):
                with pytest.raises(RuntimeError, match="no prebuilt binary found"):
                    provider("linux", "amd64", "rcoder-peer")
        finally:
            _cleanup_provider_build_dir(provider)

    @pytest.mark.skipif(not _GO_AVAILABLE, reason="go toolchain is not installed")
    def test_default_artifact_provider_builds_real_agent_binary(self) -> None:
        provider = _default_create_remote_artifact_provider(UIEventBus())
        try:
            content, content_type = provider("linux", "amd64", "rcoder-peer") or (
                None,
                None,
            )
            assert content_type == "application/octet-stream"
            assert isinstance(content, bytes)
            assert len(content) > 0
        finally:
            _cleanup_provider_build_dir(provider)

    def test_artifact_endpoint_returns_clear_error_when_unavailable(self) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(
            relay_server=relay,
            bind=f"127.0.0.1:{port}",
            artifact_provider=lambda _os_name, _arch, _name: (_ for _ in ()).throw(
                RuntimeError(
                    "peer artifact unavailable: no prebuilt binary found and local 'go' toolchain is not installed"
                )
            ),
        )
        service.start()
        try:
            try:
                _URLOPEN(
                    f"{service.base_url}/remote/artifacts/linux/amd64/rcoder-peer",
                    timeout=5,
                )
                assert False, "expected HTTPError"
            except HTTPError as exc:
                assert exc.code == 404
                body = json.loads(exc.read().decode("utf-8"))
                assert body["error"] == "artifact_unavailable"
                assert "no prebuilt binary found" in body["message"]
        finally:
            service.stop()
            relay.stop()

    @pytest.mark.skipif(not _GO_AVAILABLE, reason="go toolchain is not installed")
    def test_go_agent_end_to_end_with_http_host(self, tmp_path: Path) -> None:
        relay = RelayServer()
        relay.start()
        port = _free_port()
        service = RemoteRelayHTTPService(relay_server=relay, bind=f"127.0.0.1:{port}")
        service.start()
        agent_binary = _build_go_agent_binary()
        work_dir = tmp_path / "peer-work"
        work_dir.mkdir()
        target_file = work_dir / "demo.txt"
        target_file.write_text("hello world\n")
        proc = subprocess.Popen(
            [
                str(agent_binary),
                "--host",
                service.base_url,
                "--bootstrap-token",
                relay.issue_bootstrap_token(ttl_sec=60),
                "--cwd",
                str(work_dir),
                "--workspace-root",
                str(work_dir),
                "--poll-interval",
                "100ms",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 10
            peer_id = None
            while time.time() < deadline:
                online = relay.registry.list_online()
                if online:
                    peer_id = online[0].peer_id
                    break
                time.sleep(0.1)
            assert peer_id is not None

            backend = RemoteRelayToolBackend(relay_server=relay)
            backend.context.peer_id = peer_id

            shell_result = ShellTool(backend=backend).execute(
                command="printf 'hi-from-agent'"
            )
            assert "hi-from-agent" in shell_result

            read_result = ReadFileTool(backend=backend).execute(
                file_path=str(target_file)
            )
            assert "1\thello world" in read_result

            write_result = WriteFileTool(backend=backend).execute(
                file_path=str(target_file),
                content="alpha\nbeta\n",
            )
            assert "Wrote" in write_result
            assert target_file.read_text() == "alpha\nbeta\n"

            edit_result = EditFileTool(backend=backend).execute(
                file_path=str(target_file),
                old_string="beta",
                new_string="gamma",
            )
            assert "--- a/" in edit_result
            assert "+++ b/" in edit_result
            assert "-beta" in edit_result
            assert "+gamma" in edit_result
            assert target_file.read_text() == "alpha\ngamma\n"

            glob_result = GlobTool(backend=backend).execute(
                pattern="*.txt", path=str(work_dir)
            )
            assert str(target_file) in glob_result

            grep_result = GrepTool(backend=backend).execute(
                pattern="gamma", path=str(work_dir)
            )
            assert str(target_file) in grep_result
            assert "gamma" in grep_result
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            service.stop()
            relay.stop()
