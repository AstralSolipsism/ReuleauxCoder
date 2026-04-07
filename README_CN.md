# ReuleauxCoder

> Reinventing the wheel, but only for those who prefer it non-circular.

终端原生 AI 编程助手。

灵感来自并作为 [CoreCoder](https://github.com/he-yufeng/CoreCoder) 的完整重写而启动。

## 安装

```bash
uv sync
```

## 快速开始

```bash
# 复制示例配置
cp config.yaml.example config.yaml

# 在 config.yaml 中填入你的 API key 和模型
uv run rcoder
```

## 命令

```
/help        显示帮助
/reset       清空对话历史
/model       切换模型
/tokens      显示 token 使用量
/compact     压缩对话上下文
/save        保存会话到磁盘
/sessions    列出已保存的会话
quit         退出
```

## 许可证

AGPL-3.0-or-later
