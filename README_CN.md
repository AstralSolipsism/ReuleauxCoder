# ReuleauxCoder

> Reinventing the wheel, but only for those who prefer it non-circular.

终端原生 AI 编程助手。

灵感来自并作为 [CoreCoder](https://github.com/he-yufeng/CoreCoder) 的完整重写而启动。

## 安装

### 从 GitHub Release 安装（推荐）

先安装 [`pipx`](https://pipx.pypa.io/stable/installation/)，再用 release 中的 wheel 进行全局安装：

```bash
pipx install https://github.com/RC-CHN/ReuleauxCoder/releases/download/v0.2.8/reuleauxcoder-0.2.8-py3-none-any.whl
```

安装完成后可以直接运行：

```bash
rcoder --version
rcoder
```

### 从源码运行

```bash
uv sync
```

## 快速开始

```bash
# 将示例配置复制到工作区配置目录
mkdir -p .rcoder
cp config.yaml.example .rcoder/config.yaml

# 在 .rcoder/config.yaml 中填入你的 API key 和模型
uv run rcoder
```

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

peer MCP 不会默认从公网 `npx` / `uvx` 拉取；服务器必须提供对应平台 artifact。启动命令支持模板变量：`{{workspace}}`、`{{bundle}}`、`{{cache}}`、`{{home}}`。权限仍由服务器配置的 `/approval` 规则统一管理；需要人工确认时，确认提示会通过 remote chat 回到当前本地 peer 终端。

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
rcoder [-c CONFIG] [-m MODEL] [-p PROMPT] [-r ID]
```

- `-c, --config`：指定 `config.yaml` 路径
- `-m, --model`：覆盖配置中的模型
- `-p, --prompt`：单次提问模式（非交互）
- `-r, --resume`：按会话 ID 恢复已保存会话
- `-v, --version`：显示版本号

## 许可证

AGPL-3.0-or-later
