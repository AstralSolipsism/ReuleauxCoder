"""Provider protocols."""

from __future__ import annotations

from typing import Protocol

from reuleauxcoder.domain.providers.models import ProviderRequest, ProviderResponse


class LLMProvider(Protocol):
    """Protocol implemented by provider adapters."""

    provider_id: str
    provider_type: str

    def build_request_params(self, request: ProviderRequest) -> dict:
        """Build provider-native request parameters before hooks run."""
        ...

    def chat(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a provider request and return a provider-neutral response."""
        ...

    def test(self, *, model: str, prompt: str = "ping") -> ProviderResponse:
        """Run a minimal provider smoke test."""
        ...
