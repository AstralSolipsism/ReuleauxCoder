"""Provider adapter registry and factory."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

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

    def create(self, config: ProviderConfig, *, allow_disabled: bool = False) -> LLMProvider:
        if not config.enabled and not allow_disabled:
            raise RuntimeError(f"Provider '{config.id}' is disabled")
        cls = self._registry.get(config.type)
        if cls is None:
            raise ValueError(f"Unsupported provider type: {config.type}")
        return cls(config)

    def list_models(self, config: ProviderConfig) -> dict[str, Any]:
        if config.type == "anthropic_messages":
            return {
                "ok": True,
                "provider_id": config.id,
                "unsupported": True,
                "models": [],
                "message": "anthropic_messages provider does not expose a generic model listing endpoint.",
            }
        if config.type not in {"openai_chat", "openai_responses"}:
            raise ValueError(f"Unsupported provider type: {config.type}")

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "base_url": config.base_url,
            "timeout": config.timeout_sec,
        }
        if config.headers:
            client_kwargs["default_headers"] = config.headers
        response = OpenAI(**client_kwargs).models.list()
        raw_models = getattr(response, "data", []) or []
        models: list[dict[str, Any]] = []
        for item in raw_models:
            model_id = _model_value(item, "id")
            if not model_id:
                continue
            models.append(
                {
                    "id": str(model_id),
                    "owned_by": _model_value(item, "owned_by"),
                    "created": _model_value(item, "created"),
                }
            )
        models.sort(key=lambda model: model["id"])
        return {
            "ok": True,
            "provider_id": config.id,
            "unsupported": False,
            "models": models,
        }

    @classmethod
    def supported_types(cls) -> list[str]:
        return sorted(cls._registry)


def _model_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)
