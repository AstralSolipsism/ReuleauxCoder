# ReuleauxCoder

> Reinventing the wheel, but only for those who prefer it non-circular.

A terminal-native AI coding agent.

Inspired by and started as a complete rewrite of [CoreCoder](https://github.com/he-yufeng/CoreCoder).

## Install

### Install from GitHub Release (recommended)

Install [`pipx`](https://pipx.pypa.io/stable/installation/) first, then install the release wheel globally:

```bash
pipx install https://github.com/RC-CHN/ReuleauxCoder/releases/download/v0.2.6/reuleauxcoder-0.2.6-py3-none-any.whl
```

After installation, you can run:

```bash
rcoder --version
rcoder
```

### Run from source

```bash
uv sync
```

## Quick Start

```bash
# Copy the example config to the workspace config location
mkdir -p .rcoder
cp config.yaml.example .rcoder/config.yaml

# Edit .rcoder/config.yaml with your API key and model
uv run rcoder
```

## Remote Bootstrap (Host/Peer)

Configure remote relay in `.rcoder/config.yaml` on machine A:

```yaml
remote_exec:
  enabled: true
  host_mode: true
  relay_bind: 127.0.0.1:8765
  bootstrap_access_secret: <long-random-secret>
  bootstrap_token_ttl_sec: 120
  peer_token_ttl_sec: 3600
```

Then start host mode with:

```bash
rcoder --server
```

> Note: `--server` is still required. It enables server mode, but the relay now listens exactly on the configured `relay_bind` address.

After that, you can bootstrap a peer on machine B with:

```bash
RC_HOST="https://<HOST>" \
RC_BOOTSTRAP_SECRET='<your-bootstrap-secret>' \
sh -c 'curl -fsSL -H "X-RC-Bootstrap-Secret: ${RC_BOOTSTRAP_SECRET}" "${RC_HOST}/remote/bootstrap.sh" | sh'
```

Windows PowerShell can use:

```powershell
$env:RC_HOST = "https://<HOST>"
$env:RC_BOOTSTRAP_SECRET = "<your-bootstrap-secret>"
iex (Invoke-WebRequest -UseBasicParsing -Headers @{ "X-RC-Bootstrap-Secret" = $env:RC_BOOTSTRAP_SECRET } "${env:RC_HOST}/remote/bootstrap.ps1").Content
```

The bootstrap access secret is checked over HTTPS before the server issues a short-lived one-time bootstrap token embedded into the returned script.

> Note: the bootstrap script now includes TTY fallback handling. Even when executed via a pipe (`curl | sh`), it will try to attach interactive mode via `/dev/tty`; if no TTY is available, it automatically falls back to non-interactive mode and keeps the peer online.

### Server-managed MCP

MCP servers can be placed by runtime:

- `placement: server`: started on the machine running `rcoder --server`. Use this for GitHub, Notion, docs search, cloud services, and other MCP servers that do not need local workspace access.
- `placement: peer`: centrally managed by the server, downloaded by the remote peer from `mcp.artifact_root`, verified with `sha256`, cached under the peer workspace at `.rcoder/mcp-cache/<server>/<version>/<platform>/`, and started on the peer. Use this for filesystem, IDE, browser, localhost, and device MCP servers.
- `placement: both`: started on the server and also made available to peers through server-hosted artifacts.

Peer MCP does not fall back to public `npx` / `uvx` installs by default. The server must provide the platform artifact. Launch commands support `{{workspace}}`, `{{bundle}}`, `{{cache}}`, and `{{home}}`. Approval policy remains server-managed; when a tool requires confirmation, the approval prompt is streamed back to the active local peer terminal.

```yaml
mcp:
  artifact_root: ".rcoder/mcp-artifacts"
  servers:
    github:
      placement: server
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "<token>"
      enabled: true

    local-filesystem:
      placement: peer
      version: "1.0.0"
      requirements:
        node: "required"
        npm: "required"
      build:
        type: "node"
        package: "@modelcontextprotocol/server-filesystem"
        package_version: "1.0.0"
        bin: "mcp-server-filesystem"
      artifacts:
        linux-amd64:
          path: "local-filesystem/1.0.0/linux-amd64.tar.gz"
          sha256: "<sha256>"
          launch:
            command: "{{bundle}}/run.sh"
            args: ["--root", "{{workspace}}"]
        windows-amd64:
          path: "local-filesystem/1.0.0/windows-amd64.zip"
          sha256: "<sha256>"
          launch:
            command: "{{bundle}}/run.cmd"
            args: ["--root", "{{workspace}}"]
      permissions:
        tools:
          write_file: "require_approval"
      enabled: true
```

Node/npx peer MCP artifacts can be managed on the server without starting the
interactive agent:

```bash
rcoder mcp install-node github --package @modelcontextprotocol/server-github@latest --bin mcp-server-github
rcoder mcp install-node local-filesystem --package @modelcontextprotocol/server-filesystem@latest --bin mcp-server-filesystem --placement peer --arg=--root --arg "{{workspace}}"
rcoder mcp install-node browser --package @demo/browser-mcp@latest --bin browser-mcp --placement both
rcoder mcp artifact build-node local-filesystem --package @modelcontextprotocol/server-filesystem@latest --bin mcp-server-filesystem --platform windows-amd64 linux-amd64
rcoder mcp artifact import local-filesystem 1.0.0 windows-amd64 ./windows-amd64.zip
rcoder mcp artifact list
rcoder mcp artifact verify
```

`install-node` defaults to `placement=server`. For `peer` or `both`, if no
platform is provided, it builds `windows-amd64` and `linux-amd64` artifacts.
The lightweight Node artifact contains `package.json`, `package-lock.json`, an
offline npm cache, and a platform wrapper. The peer must have Node/npm in `PATH`;
the wrapper runs `npm ci --offline` from the server-provided cache before
starting the MCP server.

## Commands

```text
/help             Show help
/reset            Clear current in-memory conversation only
/new              Start a new conversation (auto-save previous)
/model            List model profiles and current active profile
/model <profile>  Switch to a configured model profile
/skills           Show discovered skills
/skills reload    Reload skills from disk
/skills enable <n>  Enable one skill
/skills disable <n> Disable one skill
/tokens           Show token usage
/compact          Compress conversation context
/save             Save session to disk
/sessions         List saved sessions
/session <id>     Resume a saved session in current process
/session latest   Resume the latest saved session
/approval show    Show approval rules
/approval set ... Update approval rules
/mcp show         Show MCP server status
/mcp enable <s>   Enable one MCP server
/mcp disable <s>  Disable one MCP server
/quit             Exit
/exit             Exit
```

### Command Notes

- `/reset` only clears the current in-memory conversation. It does not delete saved sessions.
- `/new` starts a fresh conversation and auto-saves the previous one first.
- `/model` lists configured model profiles from `config.yaml`; `/model <profile>` switches to one and persists the active profile.
- Model profiles may reference server-side LLM providers. The provider keeps API keys, base URLs, protocol type, and capability flags on the host, while `/model` remains the runtime switching command.
- `/skills` shows discovered skills; `/skills reload` rescans workspace/user skill directories; `/skills enable|disable <name>` persists skill state in workspace config.
- `/session <id>` resumes a saved session in the current process; `rcoder -r <id>` resumes directly on startup.
- `/approval set` currently supports targets like `tool:<name>`, `mcp`, `mcp:<server>`, and `mcp:<server>:<tool>` with actions `allow`, `warn`, `require_approval`, or `deny`.
- `/mcp enable <server>` and `/mcp disable <server>` update workspace config and try to apply the change at runtime.

## CLI Options

```bash
rcoder [-c CONFIG] [-m MODEL] [-p PROMPT] [-r ID]
```

- `-c, --config`: path to `config.yaml`
- `-m, --model`: override model from config
- `-p, --prompt`: one-shot prompt mode (non-interactive)
- `-r, --resume`: resume a saved session by ID
- `-v, --version`: show version

## Provider Management

LLM providers are stored in `providers.items`. A model profile can reference a
provider by id, while keeping model-specific settings in `models.profiles`.

```yaml
providers:
  items:
    anthropic-main:
      type: anthropic_messages
      compat: generic
      api_key: ${ANTHROPIC_API_KEY}
      capabilities:
        tools: true
        thinking: true

models:
  active_main: claude-coder
  profiles:
    claude-coder:
      provider: anthropic-main
      model: claude-sonnet-4-5
      max_tokens: 8192
```

```bash
rcoder provider record anthropic-main --type anthropic_messages --compat generic --api-key-env ANTHROPIC_API_KEY
rcoder provider list
rcoder provider test anthropic-main --model claude-sonnet-4-5
```

Supported provider types are `openai_chat`, `anthropic_messages`, and
`openai_responses`. `provider record` only writes config; `provider test` is the
explicit live smoke command.

Provider `compat` profiles describe service-specific behavior on top of the
wire protocol. Supported values are `generic`, `deepseek`, `kimi`, `glm`,
`qwen`, and `zenmux`. If omitted, EZCode infers common providers from
`base_url`; new entries should record it explicitly.

```bash
rcoder provider record deepseek --type openai_chat --compat deepseek --api-key-env DEEPSEEK_API_KEY --base-url https://api.deepseek.com --capability thinking=true
rcoder provider record kimi --type openai_chat --compat kimi --api-key-env MOONSHOT_API_KEY --base-url https://api.moonshot.ai/v1 --capability thinking=true
rcoder provider record glm --type openai_chat --compat glm --api-key-env BIGMODEL_API_KEY --base-url https://open.bigmodel.cn/api/paas/v4 --capability thinking=true
rcoder provider record qwen --type openai_chat --compat qwen --api-key-env DASHSCOPE_API_KEY --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --capability thinking=true --extra preserve_thinking=true
rcoder provider record zenmux --type openai_chat --compat zenmux --api-key-env ZENMUX_API_KEY --base-url https://zenmux.ai/api/v1 --capability thinking=true
```

Compat profiles keep provider keys and service quirks on the host. Remote peers
never receive provider API keys, base URLs, or model credentials.

## License

AGPL-3.0-or-later

