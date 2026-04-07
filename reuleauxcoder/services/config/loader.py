"""Configuration loader - loads config.yaml."""

from pathlib import Path
from typing import Optional
import yaml

from reuleauxcoder.domain.config.models import Config, MCPServerConfig
from reuleauxcoder.domain.config.schema import DEFAULTS


class ConfigLoader:
    """Loads configuration from config.yaml."""

    DEFAULT_CONFIG_PATH = Path("config.yaml")
    USER_CONFIG_PATH = Path.home() / ".reuleauxcoder" / "config.yaml"

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path

    def find_config(self) -> Optional[Path]:
        """Find the config file to use."""
        if self.config_path:
            return self.config_path

        # Check current directory first
        if self.DEFAULT_CONFIG_PATH.exists():
            return self.DEFAULT_CONFIG_PATH

        # Check user config directory
        if self.USER_CONFIG_PATH.exists():
            return self.USER_CONFIG_PATH

        return None

    def load(self) -> Config:
        """Load configuration from YAML file."""
        config_path = self.find_config()

        if config_path is None:
            raise FileNotFoundError(
                "No config.yaml found. "
                "Create one in the current directory or in ~/.reuleauxcoder/"
            )

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return self._parse_config(data)

    def _parse_config(self, data: dict) -> Config:
        """Parse YAML data into Config model."""
        app_config = data.get("app", {})
        session_config = data.get("session", {})
        cli_config = data.get("cli", {})
        mcp_config = data.get("mcp", {})

        # Parse MCP servers
        mcp_servers = []
        servers_data = mcp_config.get("servers", {})
        for name, server_data in servers_data.items():
            mcp_servers.append(MCPServerConfig.from_dict(name, server_data))

        return Config(
            model=app_config.get("model", DEFAULTS["model"]),
            api_key=app_config.get("api_key", ""),
            base_url=app_config.get("base_url"),
            max_tokens=app_config.get("max_tokens", DEFAULTS["max_tokens"]),
            temperature=app_config.get("temperature", DEFAULTS["temperature"]),
            max_context_tokens=app_config.get(
                "max_context_tokens", DEFAULTS["max_context_tokens"]
            ),
            mcp_servers=mcp_servers,
            session_auto_save=session_config.get(
                "auto_save", DEFAULTS["session_auto_save"]
            ),
            session_dir=session_config.get("dir"),
            history_file=cli_config.get("history_file"),
        )

    @classmethod
    def from_path(cls, path: Optional[Path] = None) -> Config:
        """Convenience method to load config from a path."""
        loader = cls(path)
        return loader.load()
