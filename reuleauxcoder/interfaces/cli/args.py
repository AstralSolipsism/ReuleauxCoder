"""CLI argument parsing."""

import argparse

from reuleauxcoder import __version__


def parse_args():
    parser = argparse.ArgumentParser(
        prog="rcoder",
        description="ReuleauxCoder terminal-native coding agent.",
    )
    parser.add_argument("-c", "--config", help="Path to config.yaml")
    parser.add_argument("-m", "--model", help="Override model from config.yaml")
    parser.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    parser.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run as a dedicated remote relay host",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")
    env_parser = subparsers.add_parser(
        "env", help="Record lightweight CLI environment manifest entries"
    )
    env_subparsers = env_parser.add_subparsers(dest="env_command")
    env_record = env_subparsers.add_parser(
        "record", help="Record a server-authoritative CLI environment entry"
    )
    env_record.add_argument("tool_name")
    env_record.add_argument("--command", required=True, dest="tool_command")
    env_record.add_argument("--check", required=True)
    env_record.add_argument("--install")
    env_record.add_argument("--capability", action="append", default=[])
    env_record.add_argument("--version")
    env_record.add_argument("--source")
    env_record.add_argument("--description")

    mcp_parser = subparsers.add_parser("mcp", help="Manage MCP configuration")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")

    install_node = mcp_subparsers.add_parser(
        "install-node", help="Install a Node/npx MCP server"
    )
    install_node.add_argument("server_name")
    install_node.add_argument("--package", required=True, dest="package")
    install_node.add_argument("--bin", required=True, dest="bin")
    install_node.add_argument(
        "--placement",
        choices=["server", "peer", "both"],
        default="server",
    )
    install_node.add_argument("--platform", nargs="*")
    install_node.add_argument("--arg", action="append", dest="node_arg", default=[])
    install_node.add_argument("--env", action="append", default=[])

    artifact_parser = mcp_subparsers.add_parser(
        "artifact", help="Manage server-hosted MCP artifacts"
    )
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_command")

    build_node = artifact_subparsers.add_parser(
        "build-node", help="Build a lightweight Node/npx MCP artifact"
    )
    build_node.add_argument("server_name")
    build_node.add_argument("--package", required=True, dest="package")
    build_node.add_argument("--bin", required=True, dest="bin")
    build_node.add_argument("--platform", required=True, nargs="+")

    import_artifact = artifact_subparsers.add_parser(
        "import", help="Import an existing peer MCP artifact archive"
    )
    import_artifact.add_argument("server_name")
    import_artifact.add_argument("version")
    import_artifact.add_argument("platform")
    import_artifact.add_argument("archive")

    list_artifacts = artifact_subparsers.add_parser(
        "list", help="List configured MCP artifacts"
    )
    list_artifacts.add_argument("server_name", nargs="?")

    verify_artifacts = artifact_subparsers.add_parser(
        "verify", help="Verify configured MCP artifact checksums"
    )
    verify_artifacts.add_argument("server_name", nargs="?")
    return parser.parse_args()
