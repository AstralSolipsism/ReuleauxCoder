# Dogcode / EZCode Backend

[English](README_EN.md)

Dogcode VS Code 前端配套的后端平台，负责远端 relay、会话持久化、Provider 管理、MCP 分发、Agent Runtime 和任务执行控制面。

前端仓库：<https://github.com/AstralSolipsism/dogcode>

本仓库起源于 ReuleauxCoder 的终端原生 AI 编程助手方向，但当前 `ezcode` 分支已经做了大幅扩展改造。它不再只是一个单机终端 Agent，而是面向 Dogcode 前端、远端 peer、服务端任务队列和多执行器 Agent Runtime 的后端平台。其他上游仓库发布的公开 release wheel 不适合作为本分支安装方式，因为它们不包含 Dogcode/EZCode 所需的远端会话、Webview 集成、Provider 管理、环境清单、Agent Runtime 与服务端控制面能力。

## 项目定位

- **Dogcode 后端服务**：为 VS Code/Webview 前端提供远端会话、模型调用、任务状态、环境清单和工具执行入口。
- **远端 Host/Peer relay**：服务端以 `rcoder --server` 运行，peer 通过 bootstrap token 接入，把本地工作区、终端、MCP 和 IDE 能力安全暴露给当前会话。
- **平台化 Agent Runtime**：Agent 可以绑定 runtime profile、执行器、模型、MCP、skill、credential reference、工作区策略和审批边界。
- **任务与产物控制面**：支持 task、artifact、branch、PR、review/follow-up 等独立生命周期，为远端代码任务、Agent 集群和后续调度能力打基础。
- **服务端持久化**：保留文件会话存储，同时引入 Postgres 控制面、迁移、runtime store、session store 和任务状态管理。

## 与上游的主要区别

- 从终端交互式 Agent 扩展为 Dogcode VS Code 前端的后端平台。
- 引入服务端权威配置：Provider、MCP、环境清单、Agent Runtime、approval 和 credential reference 都由 host 统一管理。
- 引入 remote session / Webview API，后端 `SessionStore` 是对话历史权威来源，UI snapshot 只保存前端展示状态。
- 引入平台管理 MCP：server/peer/both 三种 placement，peer MCP 通过服务端 artifact 下发和校验，不默认从公网临时拉取。
- 引入 Agent Runtime 抽象：支持 ReuleauxCoder、Codex CLI、Claude Code CLI、Gemini CLI 等执行器接入方向。
- 引入 Go worker/agent runtime：负责 CLI 子进程、worktree、执行环境、stderr tail、publish、repo cache 和 Codex app-server 适配。
- 引入 task/artifact 生命周期：task 完成不等于 PR 合并，代码型任务默认走 branch + PR + 用户手动 merge，非代码任务可只产出 report/comment。

## 能力概览

### 远端 Host/Peer

Host 以 server 模式运行并提供 relay API。Peer 通过 HTTPS bootstrap secret 获取短期一次性 token，接入后可以承载本地 shell、工作区文件、IDE、本机 MCP 和交互确认。

支持 Linux shell bootstrap 和 Windows PowerShell bootstrap。脚本内置 TTY 兜底：通过 `curl | sh` 执行时会优先尝试 `/dev/tty`，无可用 TTY 时降级为非交互模式并保持 peer 在线。

### Webview 会话集成

Remote relay 暴露服务端会话 API，供 Dogcode VS Code/Webview 前端使用：

- `/remote/sessions/list`：按当前 peer fingerprint 列出已保存会话。
- `/remote/sessions/load`：加载 messages、runtime state 和可选 UI snapshot。
- `/remote/sessions/new`：创建干净的服务端 session id。
- `/remote/sessions/snapshot`：保存工具卡片、trace layout 等前端展示状态。
- `/remote/sessions/delete`：删除已保存会话及其 UI snapshot。

Chat start 请求应携带当前选中的 `session_hint`。UI snapshot 只用于前端恢复展示，不注入模型上下文。

### Provider 管理

LLM provider 存在 `providers.items` 中，模型档案通过 provider id 引用服务商配置。Provider key、base URL、wire protocol、compat 和 capability flag 保留在 host 侧，远端 peer 不接收模型凭据。

当前 provider 类型包括：

- `openai_chat`
- `anthropic_messages`
- `openai_responses`

当前 compat profile 包括 `generic`、`deepseek`、`kimi`、`glm`、`qwen` 和 `zenmux`。

### 服务端 MCP 与 Peer Artifact

MCP 可以按运行位置拆分：

- `placement: server`：在运行 `rcoder --server` 的服务器上启动，适合 GitHub、Notion、文档检索、云服务等不依赖本地工作区的 MCP。
- `placement: peer`：由服务器下发 manifest，peer 从 `mcp.artifact_root` 下载版本化 artifact，校验 `sha256` 后缓存在当前工作区 `.rcoder/mcp-cache/<server>/<version>/<platform>/` 并在本机启动。
- `placement: both`：同时在服务器启动，也通过服务器托管 artifact 下发给 peer。

Peer MCP 不会默认从公网 `npx` / `uvx` 拉取。服务器必须提供对应平台 artifact。启动命令支持 `{{workspace}}`、`{{bundle}}`、`{{cache}}`、`{{home}}` 模板变量。

### 环境清单

Host 可以向远端 peer 和前端环境配置流程提供服务器权威环境清单。清单覆盖：

- CLI 工具：peer 侧应具备的命令行工具。
- MCP 服务器：来自 MCP manifest 的 server / peer / both 条目。
- Skills：用户级或项目级 skill，并显式声明检查和安装命令。

Peer 会携带有效 peer token 从 `/remote/environment/manifest` 获取合并后的环境清单。生成的环境配置 prompt 会要求智能体只检查清单内项目，并在修改 `PATH` 或 shell 启动文件前请求批准。

### Agent Runtime 与任务控制面

Agent Runtime 把 Agent 从单一 ReuleauxCoder 运行体升级为可配置工作单元。每个 Agent 可以独立配置执行器、执行位置、服务商、模型、提示词、MCP、Skill、审批边界和工作区策略。

核心语义：

- Agent 描述“谁来做事”和“允许做什么”。
- Runtime Profile 描述“怎么运行 Agent”。
- Execution Location 区分 `remote_server` 和 `local_workspace`。
- Trigger Mode 区分 `interactive_chat` 和 `issue_task`。
- Task 与 Artifact 生命周期分离，PR merge 不改写 task 已完成的执行事实。
- 远端代码任务默认使用隔离 Git worktree 和独立分支，完成后以 PR 承载审核、讨论和二次修改。

### Go Worker / 执行器抽象

`reuleauxcoder-agent` 负责执行面能力，包括：

- CLI 子进程管理、stream、cancel、timeout 和 stderr tail。
- Codex app-server、外部 CLI executor、invocation 和 publish。
- repo cache、Git worktree、branch 创建和隔离工作目录。
- per-task/per-agent 执行环境隔离。
- 与 Python 控制面交互，回传任务状态、产物和执行结果。

Python 继续作为控制面主语言，负责配置、Provider、Prompt、HTTP relay、审批、持久化和任务状态。Go 负责长生命周期执行、Git I/O、并发和进程管理。

## 部署

### 推荐：Docker 服务端部署

自部署 Dogcode/EZCode 服务端时优先使用 Docker。源码和运行状态必须放在持久化目录中；不要依赖容器内部临时文件保存配置、会话、MCP artifact 或后安装工具。

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

### Postgres 控制面

合并后的 `ezcode` 分支包含 Postgres 会话和 Agent Runtime 控制面持久化能力。需要数据库控制面时可使用仓库内 compose 文件：

```bash
cd /data/ezcode/src/docker
docker compose -f docker-compose.postgres.yml up -d
```

数据库迁移代码位于 `reuleauxcoder/infrastructure/persistence/migrations`。具体连接信息以 `.env` 和 `.rcoder/config.yaml` 中的部署配置为准。

### 本地源码开发

```bash
git clone -b ezcode https://github.com/AstralSolipsism/ReuleauxCoder.git
cd ReuleauxCoder
uv sync
uv run rcoder --version
uv run rcoder --server
```

本地安装当前 checkout：

```bash
pipx install .
```

这只适合本地开发或受控服务端构建。面向 Dogcode 前端联动的部署推荐使用 Docker host 服务。

## 远端 Bootstrap

先在 host 的 `.rcoder/config.yaml` 中配置 remote relay：

```yaml
remote_exec:
  enabled: true
  host_mode: true
  relay_bind: 127.0.0.1:8765
  bootstrap_access_secret: <长随机字符串>
  bootstrap_token_ttl_sec: 120
  peer_token_ttl_sec: 3600
```

启动 host：

```bash
rcoder --server
```

Linux/macOS peer bootstrap：

```bash
RC_HOST="https://<HOST>" \
RC_BOOTSTRAP_SECRET='<你的 bootstrap secret>' \
sh -c 'curl -fsSL -H "X-RC-Bootstrap-Secret: ${RC_BOOTSTRAP_SECRET}" "${RC_HOST}/remote/bootstrap.sh" | sh'
```

Windows PowerShell peer bootstrap：

```powershell
$env:RC_HOST = "https://<HOST>"
$env:RC_BOOTSTRAP_SECRET = "<你的 bootstrap secret>"
iex (Invoke-WebRequest -UseBasicParsing -Headers @{ "X-RC-Bootstrap-Secret" = $env:RC_BOOTSTRAP_SECRET } "${env:RC_HOST}/remote/bootstrap.ps1").Content
```

服务端会先通过 HTTPS 校验 `Bootstrap Access Secret`，校验通过后才会签发短期、一次性的 bootstrap token，并嵌入返回脚本。

## 常用命令

```text
/help               显示帮助
/reset              仅清空当前内存中的对话
/new                开启新对话，会自动保存上一段对话
/model              列出模型配置与当前激活配置
/model <profile>    切换到指定模型配置
/skills             查看已发现的 skills
/skills reload      重新扫描 skills
/skills enable <n>  启用一个 skill
/skills disable <n> 禁用一个 skill
/tokens             显示 token 使用量
/compact            压缩当前对话上下文
/save               保存会话到磁盘
/sessions           列出已保存会话
/session <id>       在当前进程中恢复指定会话
/session latest     恢复最近一次保存的会话
/approval show      显示审批规则
/approval set ...   更新审批规则
/mcp show           显示 MCP 服务器状态
/mcp enable <s>     启用一个 MCP 服务器
/mcp disable <s>    禁用一个 MCP 服务器
/mode <mode>        切换当前模式
/debug on|off       开关 LLM 调试 trace
/jobs               查看子智能体任务
/quit               退出
/exit               退出
```

CLI 参数：

```bash
rcoder [-c CONFIG] [-m MODEL] [-p PROMPT] [-r ID] [--server] {env,provider,mcp} ...
```

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

## 开发与测试

Python 相关测试：

```powershell
uv run pytest tests/domain/agent_runtime tests/services/agent_runtime tests/services/config/test_agent_runtime_config_loader.py tests/extensions/remote_exec tests/interfaces/entrypoint/test_runner_remote_exec.py tests/services/config/test_loader.py
```

Go agent 测试：

```powershell
cd reuleauxcoder-agent
go test ./...
```

## 许可证

AGPL-3.0-or-later
