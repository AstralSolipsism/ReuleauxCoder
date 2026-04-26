"""Provider adapter registry and factory."""

from __future__ import annotations

from reuleauxcoder.domain.config.models import ProviderConfig
from reuleauxcoder.domain.providers.protocols import LLMProvider
from reuleauxcoder.services.providers.adapters.anthropic_messages import (
    AnthropicMessagesProvider,
)
from reuleauxcoder.services.providers.adapters.openai_chat import OpenAIChatProvider
from reuleauxcoder.services.providers.adapters.openai_responses import (
    OpenAIResponsesProvider,
)


class ProviderManager:
    """Create provider adapters from provider configuration."""

    _registry = {
        "openai_chat": OpenAIChatProvider,
        "anthropic_messages": AnthropicMessagesProvider,
        "openai_responses": OpenAIResponsesProvider,
    }

    def create(self, config: ProviderConfig) -> LLMProvider:
        cls = self._registry.get(config.type)
        if cls is None:
            raise ValueError(f"Unsupported provider type: {config.type}")
        return cls(config)

    @classmethod
    def supported_types(cls) -> list[str]:
        return sorted(cls._registry)
