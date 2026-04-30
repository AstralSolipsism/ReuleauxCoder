# EZCode Backend

EZCode VS Code 前端配套的后端 relay host。

前端项目地址：`<EZCODE_FRONTEND_REPO_URL>`

该分支用于配合 EZCode 前端运行。其他仓库发布的公开 release wheel 不是本分支的安装方式，因为那些 wheel 不包含 EZCode 所需的远端会话、Webview、Provider 管理和环境清单集成。

## 部署

### 推荐：Docker 服务端部署

自部署 EZCode 服务端时优先使用 Docker。源码和运行状态必须放在持久化目录中；不要依赖容器内部临时文件保存配置、会话、MCP artifact 或后安装工具。

推荐宿主机目录：

```text
/data/ezcode/src              # 当前仓库 ezcode 分支的 git clone
/data/ezcode/config           # host 配置文件；自定义 compose volume 时使用
/data/ezcode/sessions         # 持久化会话状态
/data/ezcode/mcp-artifacts    # 服务端托管的 MCP artifact
/data/ezcode/tools/npm-global # 持久化后安装的 npm CLI
/data/ezcode/cache/npm        # 持久化 npm cache
/data/ezcode/home             # 需要时作为容器 HOME
```

基础部署流程：

```bash
mkdir -p /data/ezcode
git clone -b ezcode https://github.com/AstralSolipsism/ReuleauxCoder.git /data/ezcode/src
cd /data/ezcode/src/docker
cp .env.example .env
```

编辑 `.env`，至少设置：

```text
RCODER_MODEL=
RCODER_BASE_URL=
RCODER_API_KEY=
RCODER_BOOTSTRAP_ACCESS_SECRET=
RCODER_ADMIN_ACCESS_SECRET=
```

启动 host：

```bash
docker compose up -d --build
docker compose logs -f rcoder-host
```

默认 compose 会把宿主机 `../.rcoder` 挂载到容器 `/app/.rcoder`。生产环境必须确保这个宿主机目录在持久化磁盘上；也可以在首次启动前把 volume 改成自己的 `/data/ezcode/config` 持久化路径。

### 本地源码开发

本地开发后端时使用：

```bash
git clone -b ezcode https://github.com/AstralSolipsism/ReuleauxCoder.git
cd ReuleauxCoder
uv sync
uv run rcoder --version
uv run rcoder --server
```

### 可选：本地 pipx 安装

如果只想从当前本地 checkout 安装，不发布 release wheel：

```bash
pipx install .
```

这只适合本地开发或受控服务端构建。面向前端联动的 EZCode 推荐使用 Docker host 服务部署。

## 远端 Bootstrap（Host/Peer）

先在 A 机的 `.rcoder/config.yaml` 中配置 remote relay：

```yaml
remote_exec:
  enabled: true
  host_mode: true
  relay_bind: 127.0.0.1:8765
  bootstrap_access_secret: <长随机字符串>
  bootstrap_token_ttl_sec: 120
  peer_token_ttl_sec: 3600
```

然后用下面命令启动 host 模式：

```bash
rcoder --server
```

> 注意：`--server` 仍然是必须的。它会开启 server mode，但 relay 实际监听地址会严格按 `relay_bind` 配置生效。

之后可以在 B 机通过一条命令拉起 peer：

```bash
RC_HOST="https://<HOST>" \
RC_BOOTSTRAP_SECRET='<你的 bootstrap secret>' \
sh -c 'curl -fsSL -H "X-RC-Bootstrap-Secret: ${RC_BOOTSTRAP_SECRET}" "${RC_HOST}/remote/bootstrap.sh" | sh'
```

Windows PowerShell 可以使用：

```powershell
$env:RC_HOST = "https://<HOST>"
$env:RC_BOOTSTRAP_SECRET = "<你的 bootstrap secret>"
iex (Invoke-WebRequest -UseBasicParsing -Headers @{ "X-RC-Bootstrap-Secret" = $env:RC_BOOTSTRAP_SECRET } "${env:RC_HOST}/remote/bootstrap.ps1").Content
```

服务端会先通过 HTTPS 校验 `Bootstrap Access Secret`，校验通过后才会签发一个短期、一次性的 bootstrap token，并嵌入返回的脚本中。

> 注意：脚本已内置 TTY 兜底处理。即使通过 pipe 执行（`curl | sh`），也会优先尝试从 `/dev/tty` 进入 `--interactive`；若无可用 TTY，则自动降级为非交互模式并保持 peer 在线。

### 服务器统一管理 MCP

MCP 可以按运行位置拆分：

- `placement: server`：在运行 `rcoder --server` 的服务器上启动，适合 GitHub、Notion、文档检索、云服务等不依赖本地工作区的 MCP。
- `placement: peer`：由服务器下发 manifest，peer 从服务器 `mcp.artifact_root` 下载版本化 artifact，校验 `sha256` 后缓存在当前工作区 `.rcoder/mcp-cache/<server>/<version>/<platform>/` 并在本机启动，适合文件系统、IDE、浏览器、localhost、设备等必须访问本地资源的 MCP。
- `placement: both`：同时在服务器启动，也通过服务器托管 artifact 下发给 peer。

peer MCP 不会默认从公网 `npx` / `uvx` 拉取；服务器必须提供对应平台 artifact。启动命令支持模板变量：`{{workspace}}`、`{{bundle}}`、`{{cache}}`、`{{home}}`。权限仍由服务器配置的 `/approval` 规则统一管理；需要人工确认时，确认提示会回到当前活跃的交互客户端，包括 CLI、peer 终端或 VS Code Webview 集成。

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

Node/npx 类 peer MCP 可以在服务器上用独立命令管理，不需要启动交互 agent：

```bash
rcoder mcp install-node github --package @modelcontextprotocol/server-github@latest --bin mcp-server-github
rcoder mcp install-node local-filesystem --package @modelcontextprotocol/server-filesystem@latest --bin mcp-server-filesystem --placement peer --arg=--root --arg "{{workspace}}"
rcoder mcp install-node browser --package @demo/browser-mcp@latest --bin browser-mcp --placement both
rcoder mcp artifact build-node local-filesystem --package @modelcontextprotocol/server-filesystem@latest --bin mcp-server-filesystem --platform windows-amd64 linux-amd64
rcoder mcp artifact import local-filesystem 1.0.0 windows-amd64 ./windows-amd64.zip
rcoder mcp artifact list
rcoder mcp artifact verify
```

`install-node` 默认 `placement=server`。当选择 `peer` 或 `both` 且未指定平台时，会默认生成 `windows-amd64` 与 `linux-amd64` artifact。
轻量 Node artifact 包含 `package.json`、`package-lock.json`、离线 npm cache 和平台 wrapper。peer 需要本机 `PATH` 中已有 Node/npm；wrapper 会先使用服务器提供的 cache 执行 `npm ci --offline`，再启动 MCP server。

### 环境清单

Host 可以向远端 peer 和前端环境配置流程提供服务器权威环境清单。清单分为三类：

- CLI 工具：peer 侧应具备的命令行工具。
- MCP 服务器：来自 MCP manifest 的 server / peer / both 条目。
- Skills：用户级或项目级 skill，并显式声明检查和安装命令。

CLI 工具可以在不启动交互 agent 的情况下登记：

```bash
rcoder env record gitnexus \
  --command gitnexus \
  --check "gitnexus --version" \
  --install "npm install -g gitnexus" \
  --capability repo_index \
  --source npm \
  --description "Repository graph CLI"
```

Skills 可配置在 `environment.skills`：

```yaml
environment:
  skills:
    collaborating-with-claude:
      scope: user
      check: "Test-Path $env:USERPROFILE/.agents/skills/collaborating-with-claude/SKILL.md"
      install: "python C:/Users/you/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py --repo your/repo --path skills/collaborating-with-claude"
      source: github
      description: "Claude collaboration skill"
      path_hint: "~/.agents/skills/collaborating-with-claude/SKILL.md"
```

peer 会携带有效 peer token 从 `/remote/environment/manifest` 获取合并后的环境清单。生成的环境配置 prompt 会要求智能体只检查清单内项目，并且在修改 `PATH` 或 shell 启动文件前请求批准。

## 命令

```text
/help             显示帮助
/reset            仅清空当前内存中的对话
/new              开启新对话（会自动保存上一段对话）
/model            列出模型配置与当前激活配置
/model <profile>  切换到指定模型配置
/skills           查看已发现的 skills
/skills reload    重新扫描 skills
/skills enable <n>  启用一个 skill
/skills disable <n> 禁用一个 skill
/tokens           显示 token 使用量
/compact          压缩当前对话上下文
/save             保存会话到磁盘
/sessions         列出已保存会话
/session <id>     在当前进程中恢复指定会话
/session latest   恢复最近一次保存的会话
/approval show    显示审批规则
/approval set ... 更新审批规则
/mcp show         显示 MCP 服务器状态
/mcp enable <s>   启用一个 MCP 服务器
/mcp disable <s>  禁用一个 MCP 服务器
/mode <mode>      切换当前模式
/debug on|off     开关 LLM 调试 trace
/jobs             查看子智能体任务
/quit             退出
/exit             退出
```

### 命令说明

- `/reset` 只会清空当前内存中的对话，不会删除已保存的会话。
- `/new` 会先自动保存上一段对话，再开启一段新的对话。
- `/model` 会列出 `config.yaml` 中配置的模型档案；`/model <profile>` 会切换并持久化当前激活档案。
- `/skills` 会展示当前发现的 skills；`/skills reload` 会重新扫描工作区和用户目录；`/skills enable|disable <name>` 会把状态持久化到工作区配置。
- `/session <id>` 会在当前进程中恢复会话；也可以用 `rcoder -r <id>` 在启动时直接恢复。
- `/approval set` 当前支持的目标格式包括 `tool:<name>`、`mcp`、`mcp:<server>`、`mcp:<server>:<tool>`；动作支持 `allow`、`warn`、`require_approval`、`deny`。
- `/mcp enable <server>` 与 `/mcp disable <server>` 会更新工作区配置，并尝试在运行时立即生效。

## CLI 参数

```bash
rcoder [-c CONFIG] [-m MODEL] [-p PROMPT] [-r ID] [--server] {env,provider,mcp} ...
```

- `-c, --config`：指定 `config.yaml` 路径
- `-m, --model`：覆盖配置中的模型
- `-p, --prompt`：单次提问模式（非交互）
- `-r, --resume`：按会话 ID 恢复已保存会话
- `--server`：作为专用远端 relay host 运行
- `-v, --version`：显示版本号

非交互管理子命令：

```bash
rcoder env record ...
rcoder provider list
rcoder provider record ...
rcoder provider test ...
rcoder mcp record ...
rcoder mcp install-node ...
rcoder mcp artifact build-node|import|list|verify ...
```

## Provider 管理

LLM provider 存在 `providers.items` 中。模型档案可以通过 provider id 引用服务商配置，同时把模型级参数保留在 `models.profiles`。

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

当前支持的 provider 类型包括 `openai_chat`、`anthropic_messages` 和 `openai_responses`。`provider record` 只写入配置；`provider test` 是显式在线 smoke test。

Provider `compat` 用来描述 wire protocol 之上的服务商差异。支持值包括 `generic`、`deepseek`、`kimi`、`glm`、`qwen` 和 `zenmux`。如果省略，EZCode 会根据 `base_url` 推断常见服务商；新条目建议显式记录。

```bash
rcoder provider record deepseek --type openai_chat --compat deepseek --api-key-env DEEPSEEK_API_KEY --base-url https://api.deepseek.com --capability thinking=true
rcoder provider record kimi --type openai_chat --compat kimi --api-key-env MOONSHOT_API_KEY --base-url https://api.moonshot.ai/v1 --capability thinking=true
rcoder provider record glm --type openai_chat --compat glm --api-key-env BIGMODEL_API_KEY --base-url https://open.bigmodel.cn/api/paas/v4 --capability thinking=true
rcoder provider record qwen --type openai_chat --compat qwen --api-key-env DASHSCOPE_API_KEY --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --capability thinking=true --extra preserve_thinking=true
rcoder provider record zenmux --type openai_chat --compat zenmux --api-key-env ZENMUX_API_KEY --base-url https://zenmux.ai/api/v1 --capability thinking=true
```

Compat profile 会把 provider key 和服务商差异保留在 host 侧。远端 peer 不会收到 provider API key、base URL 或模型凭据。

## 远端会话与前端集成

Remote relay 暴露服务端会话 API，供 VS Code / Webview 前端使用：

- `/remote/sessions/list`：按当前 peer fingerprint 列出已保存会话。
- `/remote/sessions/load`：加载 messages、runtime state 和可选 UI snapshot。
- `/remote/sessions/new`：创建干净的服务端 session id。
- `/remote/sessions/snapshot`：保存工具卡片、trace layout 等前端展示状态。
- `/remote/sessions/delete`：删除已保存会话及其 UI snapshot。

Chat start 请求应携带当前选中的 `session_hint`。后端 `SessionStore` 是对话历史的唯一权威来源；UI snapshot 只用于前端恢复展示，不会注入模型上下文。

## 许可证

AGPL-3.0-or-later
