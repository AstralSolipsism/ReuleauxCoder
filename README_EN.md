# Dogcode / EZCode Backend

[中文](README.md)

Backend platform for the Dogcode VS Code frontend.

Frontend repository: <https://github.com/AstralSolipsism/dogcode>

This repository started from the ReuleauxCoder terminal-native coding-agent direction, but the `ezcode` branch has diverged substantially. It is now the backend platform for Dogcode/EZCode: remote relay host, server-backed sessions, provider management, MCP distribution, environment manifests, Agent Runtime, task control plane, and Go-based execution workers.

Public release wheels from upstream or sibling repositories are not the installation path for this branch because they do not include the Dogcode/EZCode remote-session, Webview, provider, environment-manifest, Agent Runtime, and control-plane integrations.

## What This Branch Adds

- Dogcode VS Code/Webview backend APIs for remote sessions and UI snapshots.
- Remote Host/Peer relay with bootstrap tokens and interactive approval routing.
- Server-authoritative provider/model configuration.
- Server-managed MCP with `server`, `peer`, and `both` placement modes.
- Environment manifests for peer-side CLI tools, MCP servers, and skills.
- Agent Runtime configuration for agents, runtime profiles, execution locations, capabilities, credentials, prompts, MCP, and skills.
- Task and artifact lifecycles where task completion, PR review, and PR merge are separate states.
- Git worktree and PR-oriented delivery for remote code tasks.
- Postgres-backed control-plane persistence.
- Go worker/runtime components for CLI executors, subprocess management, repo cache, worktrees, and publishing.

## Deployment

Use Docker for a self-hosted Dogcode/EZCode server. Keep the source checkout and runtime state on persistent storage.

Recommended host layout:

```text
/data/ezcode/src              # git clone of this repository, branch ezcode
/data/ezcode/config           # host config files, if compose volumes are customized
/data/ezcode/sessions         # persisted session state
/data/ezcode/mcp-artifacts    # server-hosted MCP artifacts
/data/ezcode/tools/npm-global # persistent post-installed npm CLIs
/data/ezcode/cache/npm        # persistent npm cache
/data/ezcode/home             # container HOME when needed
```

Basic deployment:

```bash
mkdir -p /data/ezcode
git clone -b ezcode https://github.com/AstralSolipsism/ReuleauxCoder.git /data/ezcode/src
cd /data/ezcode/src/docker
cp .env.example .env
```

Edit `.env` and set at least:

```text
RCODER_MODEL=
RCODER_BASE_URL=
RCODER_API_KEY=
RCODER_BOOTSTRAP_ACCESS_SECRET=
RCODER_ADMIN_ACCESS_SECRET=
```

Start the host:

```bash
docker compose up -d --build
docker compose logs -f rcoder-host
```

## Remote Bootstrap

Configure remote relay in `.rcoder/config.yaml` on the host:

```yaml
remote_exec:
  enabled: true
  host_mode: true
  relay_bind: 127.0.0.1:8765
  bootstrap_access_secret: <long-random-secret>
  bootstrap_token_ttl_sec: 120
  peer_token_ttl_sec: 3600
```

Start host mode:

```bash
rcoder --server
```

Bootstrap a Linux/macOS peer:

```bash
RC_HOST="https://<HOST>" \
RC_BOOTSTRAP_SECRET='<your-bootstrap-secret>' \
sh -c 'curl -fsSL -H "X-RC-Bootstrap-Secret: ${RC_BOOTSTRAP_SECRET}" "${RC_HOST}/remote/bootstrap.sh" | sh'
```

Bootstrap a Windows PowerShell peer:

```powershell
$env:RC_HOST = "https://<HOST>"
$env:RC_BOOTSTRAP_SECRET = "<your-bootstrap-secret>"
iex (Invoke-WebRequest -UseBasicParsing -Headers @{ "X-RC-Bootstrap-Secret" = $env:RC_BOOTSTRAP_SECRET } "${env:RC_HOST}/remote/bootstrap.ps1").Content
```

## Development

```bash
git clone -b ezcode https://github.com/AstralSolipsism/ReuleauxCoder.git
cd ReuleauxCoder
uv sync
uv run rcoder --version
uv run rcoder --server
```

Run targeted Python tests:

```powershell
uv run pytest tests/domain/agent_runtime tests/ezcode_server tests/services/config/test_agent_runtime_config_loader.py tests/interfaces/entrypoint/test_runner_remote_exec.py tests/services/config/test_loader.py
```

Run Go agent tests:

```powershell
cd reuleauxcoder-agent
go test ./...
```

## License

AGPL-3.0-or-later
