# Labrastro Backend Foundation

[中文](README.md)

This repository is the backend foundation for the Labrastro ecosystem. It is derived from the [RC-CHN/ReuleauxCoder](https://github.com/RC-CHN/ReuleauxCoder) fork lineage, keeps the ReuleauxCoder kernel boundary intact, and adds Labrastro-specific remote relay, session persistence, provider management, MCP distribution, environment manifests, Agent Runtime, and task control plane.

Repository: <https://github.com/AstralSolipsism/Labrastro>

## Naming Boundary

The upstream ReuleauxCoder boundary is preserved:

- Python kernel package: `reuleauxcoder`
- CLI: `rcoder`
- Config directory: `.rcoder`
- Local peer artifact: `rcoder-peer`
- Go worker directory and module: `reuleauxcoder-agent`
- Native Agent Runtime executor id: `reuleauxcoder`
- HTTP headers: `X-RC-*`

Labrastro-owned control-plane names use the new brand:

- Python control-plane package: `labrastro_server`
- Default Docker image/container: `labrastro-host`
- Default database name, user, and volume: `labrastro`
- Database environment variables: `LABRASTRO_DATABASE_URL`, `LABRASTRO_AUTO_MIGRATE`, `LABRASTRO_TEST_DATABASE_URL`

## Capabilities

- **Labrastro backend foundation** for remote sessions, model calls, task state, environment manifests, and tool execution entrypoints.
- **Remote Host/Peer relay** where the host runs as `rcoder --server` and peers join through bootstrap tokens.
- **Agent Runtime control plane** for runtime profiles, executors, models, MCP, skills, credentials, workspace policies, and approval boundaries.
- **Task and artifact lifecycle** for task, artifact, branch, PR, review, and follow-up states.
- **Server-side persistence** with file session storage plus Postgres migrations, runtime store, session store, and task state management.
- **Go worker execution surface** through `reuleauxcoder-agent` for CLI subprocesses, worktrees, repo cache, publishing, and long-running tasks.

## Deployment

Use Docker for a self-hosted Labrastro backend. Keep the source checkout and runtime state on persistent storage.

Recommended host layout:

```text
/data/labrastro/src              # git clone of this repository
/data/labrastro/config           # host config files, if compose volumes are customized
/data/labrastro/sessions         # persisted session state
/data/labrastro/mcp-artifacts    # server-hosted MCP artifacts
/data/labrastro/tools/npm-global # persistent post-installed npm CLIs
/data/labrastro/cache/npm        # persistent npm cache
/data/labrastro/home             # container HOME when needed
```

Basic deployment:

```bash
mkdir -p /data/labrastro
git clone https://github.com/AstralSolipsism/Labrastro.git /data/labrastro/src
cd /data/labrastro/src/docker
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
docker compose logs -f labrastro-host
```

For Postgres-backed control-plane state:

```bash
cd /data/labrastro/src/docker
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

The config template reads `LABRASTRO_DATABASE_URL` for database connectivity.

## Remote Bootstrap

Configure remote relay in `.rcoder/config.yaml` on the host:

```yaml
remote_exec:
  enabled: true
  host_mode: true
  relay_bind: 127.0.0.1:8765
  bootstrap_access_secret: <long-random-secret>
  admin_access_secret: <long-random-secret>
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
git clone https://github.com/AstralSolipsism/Labrastro.git
cd Labrastro
uv sync
uv run rcoder --version
uv run rcoder --server
```

Run tests:

```powershell
uv run pytest -q

cd reuleauxcoder-agent
go test ./...
```

## License

AGPL-3.0-or-later
