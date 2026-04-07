"""ReuleauxCoder - A terminal-native coding agent framework.

Reinventing the wheel, but only for those who prefer it non-circular.
"""

__version__ = "0.1.0"

from reuleauxcoder.domain.agent import Agent
from reuleauxcoder.services.llm.client import LLM
from reuleauxcoder.domain.config.models import Config
from reuleauxcoder.extensions.tools.registry import ALL_TOOLS

__all__ = ["Agent", "LLM", "Config", "ALL_TOOLS", "__version__"]
