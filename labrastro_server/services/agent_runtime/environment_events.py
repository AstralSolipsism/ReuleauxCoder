"""Typed environment events derived from normal runtime executor events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from labrastro_server.services.agent_runtime.executor_backend import (
    ExecutorEvent,
    ExecutorEventType,
)


@dataclass(frozen=True)
class EnvironmentEventExpansion:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    policy_error: str = ""


def expand_environment_executor_event(
    task_metadata: dict[str, Any], event: ExecutorEvent
) -> EnvironmentEventExpansion:
    """Return environment typed events and policy violations for an executor event."""

    if task_metadata.get("workflow") != "environment_config":
        return EnvironmentEventExpansion()
    command = _extract_command(event.data)
    if not command:
        return EnvironmentEventExpansion()
    allowed = _allowed_command_map(task_metadata.get("allowed_commands"))
    command_key = _command_key(command)
    command_meta = allowed.get(command_key)
    if command_meta is None:
        return EnvironmentEventExpansion(
            events=[
                (
                    "environment.entry_failed",
                    {
                        "command": command,
                        "error": "command is not declared in the environment manifest allowlist",
                        "error_code": "environment_command_not_allowed",
                    },
                )
            ],
            policy_error=f"environment command is not allowlisted: {command}",
        )
    if (
        str(task_metadata.get("environment_mode") or "") == "check"
        and command_meta.get("phase") == "install"
    ):
        return EnvironmentEventExpansion(
            events=[
                (
                    "environment.entry_failed",
                    {
                        **command_meta,
                        "command": command,
                        "error": "check mode cannot run install commands",
                        "error_code": "environment_install_forbidden_in_check",
                    },
                )
            ],
            policy_error=f"environment check mode attempted install command: {command}",
        )

    phase = str(command_meta.get("phase") or "")
    payload = {**command_meta, "command": command}
    events: list[tuple[str, dict[str, Any]]] = []
    if event.type == ExecutorEventType.TOOL_USE:
        if phase == "install":
            events.append(("environment.install_requested", dict(payload)))
        events.append(("environment.entry_started", dict(payload)))
    elif event.type == ExecutorEventType.TOOL_RESULT:
        ok = _result_ok(event.data)
        result_payload = {**payload, "ok": ok}
        output = _result_output(event.data)
        if output:
            result_payload["output"] = output
        if phase == "check":
            events.append(("environment.entry_checked", dict(result_payload)))
            if ok is True:
                events.append(("environment.entry_verified", dict(result_payload)))
            elif ok is False:
                events.append(("environment.entry_failed", dict(result_payload)))
        elif phase == "install" and ok is False:
            events.append(("environment.entry_failed", dict(result_payload)))
    return EnvironmentEventExpansion(events=events)


def environment_summary_event(
    task_metadata: dict[str, Any], status: str, output: str = "", error: str = ""
) -> tuple[str, dict[str, Any]] | None:
    if task_metadata.get("workflow") != "environment_config":
        return None
    return (
        "environment.summary",
        {
            "status": status,
            "environment_mode": str(task_metadata.get("environment_mode") or ""),
            "entry_ids": list(task_metadata.get("entry_ids") or []),
            "manifest_hash": str(task_metadata.get("manifest_hash") or ""),
            "output": output,
            "error": error,
        },
    )


def _allowed_command_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "") or "").strip()
        if not command:
            continue
        out[_command_key(command)] = {
            key: str(val)
            for key, val in item.items()
            if val is not None and key != "command"
        }
    return out


def _command_key(command: str) -> str:
    return str(command or "").strip()


def _extract_command(value: Any) -> str:
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        for item in value:
            command = _extract_command(item)
            if command:
                return command
        return ""
    if not isinstance(value, dict):
        return ""
    for key in (
        "command",
        "cmd",
        "shell_command",
        "shellCommand",
        "command_line",
        "commandLine",
    ):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    argv = value.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(part) for part in argv if str(part).strip()).strip()
    for key in ("input", "output", "item", "params", "arguments", "request"):
        command = _extract_command(value.get(key))
        if command:
            return command
    return ""


def _result_ok(data: dict[str, Any]) -> bool | None:
    value = _nested_value(
        data,
        (
            ("exit_code",),
            ("exitCode",),
            ("output", "exit_code"),
            ("output", "exitCode"),
            ("item", "exitCode"),
            ("input", "exitCode"),
            ("input", "exit_code"),
        ),
    )
    if value is not None:
        try:
            return int(value) == 0
        except (TypeError, ValueError):
            pass
    value = _nested_value(
        data,
        (
            ("ok",),
            ("success",),
            ("output", "ok"),
            ("output", "success"),
            ("item", "success"),
            ("input", "success"),
        ),
    )
    if isinstance(value, bool):
        return value
    status = _nested_value(
        data,
        (("status",), ("output", "status"), ("item", "status"), ("input", "status")),
    )
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized in {"ok", "success", "succeeded", "completed"}:
            return True
        if normalized in {"error", "failed", "failure"}:
            return False
    return None


def _result_output(data: dict[str, Any]) -> str:
    value = _nested_value(
        data,
        (
            ("output", "text"),
            ("output", "aggregatedOutput"),
            ("input", "aggregatedOutput"),
            ("stdout",),
            ("stderr",),
            ("aggregatedOutput",),
            ("item", "aggregatedOutput"),
            ("output",),
        ),
    )
    if isinstance(value, str):
        return value
    return ""


def _nested_value(data: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        cur: Any = data
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if cur is not None:
            return cur
    return None
