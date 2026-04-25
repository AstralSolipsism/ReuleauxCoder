"""Bootstrap script generation for remote peers."""

from __future__ import annotations


DEFAULT_ARTIFACT_PATH_TEMPLATE = "/remote/artifacts/{os}/{arch}/rcoder-peer"


BOOTSTRAP_SCRIPT_TEMPLATE = """#!/bin/sh
set -eu

# ReuleauxCoder remote bootstrap agent
TMPDIR="${TMPDIR:-/tmp}"
WORKDIR="$(mktemp -d "${TMPDIR}/rc-peer.XXXXXX")"
trap 'rm -rf "${WORKDIR}"' EXIT INT TERM

HOST="${RC_HOST:-{{host}}}"
TOKEN="${RC_TOKEN:-{{token}}}"
BIN="${WORKDIR}/rcoder-peer"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

ARTIFACT_PATH="{{artifact_path}}"
ARTIFACT_PATH="$(printf '%s' "$ARTIFACT_PATH" | sed "s/{os}/$OS/g" | sed "s/{arch}/$ARCH/g")"
ARTIFACT_URL="${HOST}${ARTIFACT_PATH}"

curl -fsSL "$ARTIFACT_URL" -o "$BIN"
chmod +x "$BIN"

# Keep interactive mode working when script is executed via pipe, e.g.
#   curl .../remote/bootstrap.sh | sh
if [ -t 0 ]; then
  exec "$BIN" --host "$HOST" --bootstrap-token "$TOKEN" --interactive
fi

if [ -r /dev/tty ]; then
  exec "$BIN" --host "$HOST" --bootstrap-token "$TOKEN" --interactive </dev/tty
fi

echo "[bootstrap] no TTY available; starting peer in non-interactive mode" >&2
exec "$BIN" --host "$HOST" --bootstrap-token "$TOKEN"
"""


POWERSHELL_BOOTSTRAP_SCRIPT_TEMPLATE = """$ErrorActionPreference = "Stop"

# ReuleauxCoder remote bootstrap agent
$RcHost = if ($env:RC_HOST) { ($env:RC_HOST).TrimEnd("/") } else { "{{host}}" }
$Token = if ($env:RC_TOKEN) { $env:RC_TOKEN } else { "{{token}}" }
$TmpRoot = if ($env:TEMP) { $env:TEMP } else { [System.IO.Path]::GetTempPath() }
$WorkDir = Join-Path $TmpRoot ("rc-peer." + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

try {
    $Bin = Join-Path $WorkDir "rcoder-peer.exe"
    $ArchRaw = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($ArchRaw.ToLowerInvariant()) {
        "amd64" { $Arch = "amd64" }
        "arm64" { $Arch = "arm64" }
        "aarch64" { $Arch = "arm64" }
        default {
            Write-Error "Unsupported architecture: $ArchRaw"
            exit 1
        }
    }

    $ArtifactPath = "{{artifact_path}}".Replace("{os}", "windows").Replace("{arch}", $Arch)
    $ArtifactUrl = "$RcHost$ArtifactPath"

    Invoke-WebRequest -Uri $ArtifactUrl -OutFile $Bin -UseBasicParsing
    Unblock-File -Path $Bin -ErrorAction SilentlyContinue
    & $Bin --host $RcHost --bootstrap-token $Token --interactive
}
finally {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def generate_bootstrap_script(
    host: str,
    token: str,
    heartbeat_interval_sec: int = 10,
    artifact_path_template: str = DEFAULT_ARTIFACT_PATH_TEMPLATE,
) -> str:
    """Generate a POSIX shell bootstrap script for the remote peer."""
    del heartbeat_interval_sec  # reserved for future peer flags
    script = BOOTSTRAP_SCRIPT_TEMPLATE.replace("{{host}}", host.rstrip("/"))
    script = script.replace("{{token}}", token)
    script = script.replace("{{artifact_path}}", artifact_path_template)
    return script


def generate_powershell_bootstrap_script(
    host: str,
    token: str,
    artifact_path_template: str = DEFAULT_ARTIFACT_PATH_TEMPLATE,
) -> str:
    """Generate a PowerShell bootstrap script for Windows remote peers."""
    script = POWERSHELL_BOOTSTRAP_SCRIPT_TEMPLATE.replace("{{host}}", host.rstrip("/"))
    script = script.replace("{{token}}", token)
    script = script.replace("{{artifact_path}}", artifact_path_template)
    return script
