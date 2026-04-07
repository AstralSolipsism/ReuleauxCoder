"""LLM client - wraps OpenAI-compatible APIs."""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, List

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError

from reuleauxcoder.domain.llm.models import ToolCall, LLMResponse


class LLM:
    """LLM client that wraps OpenAI-compatible APIs."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Token tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if tools:
            params["tools"] = tools

        # stream_options is an OpenAI extension
        try:
            params["stream_options"] = {"include_usage": True}
            stream = self._call_with_retry(params)
        except Exception:
            params.pop("stream_options", None)
            stream = self._call_with_retry(params)

        # Accumulate response
        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0

        for chunk in stream:
            # Usage info comes in the final chunk
            if chunk.usage:
                prompt_tok = chunk.usage.prompt_tokens
                completion_tok = chunk.usage.completion_tokens

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Accumulate text
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # Accumulate tool calls across chunks
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        # Parse accumulated tool calls
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        # Update token counts
        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2**attempt
                time.sleep(wait)
