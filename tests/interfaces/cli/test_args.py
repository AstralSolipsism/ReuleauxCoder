import sys

import pytest

from reuleauxcoder.interfaces.cli.args import parse_args


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["rcoder"])
    args = parse_args()
    assert args.config is None
    assert args.model is None
    assert args.prompt is None
    assert args.resume is None


def test_parse_args_all_supported_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rcoder",
            "-c",
            "config.yaml",
            "-m",
            "gpt-4o",
            "-p",
            "hello",
            "-r",
            "session-1",
        ],
    )
    args = parse_args()
    assert args.config == "config.yaml"
    assert args.model == "gpt-4o"
    assert args.prompt == "hello"
    assert args.resume == "session-1"


def test_parse_mcp_artifact_build_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rcoder",
            "-c",
            "config.yaml",
            "mcp",
            "artifact",
            "build-node",
            "filesystem",
            "--package",
            "@demo/filesystem@latest",
            "--bin",
            "filesystem-mcp",
            "--platform",
            "windows-amd64",
            "linux-amd64",
        ],
    )

    args = parse_args()

    assert args.config == "config.yaml"
    assert args.command == "mcp"
    assert args.mcp_command == "artifact"
    assert args.artifact_command == "build-node"
    assert args.server_name == "filesystem"
    assert args.package == "@demo/filesystem@latest"
    assert args.bin == "filesystem-mcp"
    assert args.platform == ["windows-amd64", "linux-amd64"]


def test_parse_mcp_install_node_defaults_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rcoder",
            "mcp",
            "install-node",
            "github",
            "--package",
            "@demo/github@latest",
            "--bin",
            "github-mcp",
        ],
    )

    args = parse_args()

    assert args.command == "mcp"
    assert args.mcp_command == "install-node"
    assert args.server_name == "github"
    assert args.placement == "server"
    assert args.platform is None
    assert args.node_arg == []
    assert args.env == []


def test_parse_mcp_install_node_peer_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rcoder",
            "mcp",
            "install-node",
            "filesystem",
            "--package",
            "@demo/filesystem@latest",
            "--bin",
            "filesystem-mcp",
            "--placement",
            "both",
            "--platform",
            "windows-amd64",
            "--arg=--root",
            "--arg",
            "{{workspace}}",
            "--env",
            "MODE=local",
        ],
    )

    args = parse_args()

    assert args.placement == "both"
    assert args.platform == ["windows-amd64"]
    assert args.node_arg == ["--root", "{{workspace}}"]
    assert args.env == ["MODE=local"]


def test_parse_args_version_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["rcoder", "--version"])
    with pytest.raises(SystemExit):
        parse_args()
