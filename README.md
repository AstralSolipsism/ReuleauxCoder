# ReuleauxCoder

> Reinventing the wheel, but only for those who prefer it non-circular.

A terminal-native AI coding agent.

Inspired by and started as a complete rewrite of [CoreCoder](https://github.com/he-yufeng/CoreCoder).

## Install

```bash
uv sync
```

## Quick Start

```bash
# Copy the example config
cp config.yaml.example config.yaml

# Edit config.yaml with your API key and model
uv run rcoder
```

## Commands

```
/help        Show help
/reset       Clear conversation history
/model       Switch model mid-conversation
/tokens      Show token usage
/compact     Compress conversation context
/save        Save session to disk
/sessions    List saved sessions
quit         Exit
```

## License

AGPL-3.0-or-later
