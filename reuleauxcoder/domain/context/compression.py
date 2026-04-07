"""Compression strategies for context management."""

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from reuleauxcoder.services.llm.client import LLM


class CompressionStrategy(ABC):
    """Base class for compression strategies."""

    @abstractmethod
    def compress(self, messages: list[dict], llm: Optional["LLM"] = None) -> bool:
        """Apply compression to messages.

        Returns True if compression was applied.
        """
        ...


class ToolOutputSnipStrategy(CompressionStrategy):
    """Layer 1: Snip verbose tool outputs."""

    THRESHOLD = 1500  # chars
    KEEP_LINES = 3

    def compress(self, messages: list[dict], llm: Optional["LLM"] = None) -> bool:
        """Truncate tool results over threshold."""
        changed = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) <= self.THRESHOLD:
                continue
            lines = content.splitlines()
            if len(lines) <= 6:
                continue
            snipped = (
                "\n".join(lines[: self.KEEP_LINES])
                + f"\n... ({len(lines)} lines, snipped) ...\n"
                + "\n".join(lines[-self.KEEP_LINES :])
            )
            m["content"] = snipped
            changed = True
        return changed


class SummarizeStrategy(CompressionStrategy):
    """Layer 2: LLM-powered summarization."""

    KEEP_RECENT = 8

    def compress(self, messages: list[dict], llm: Optional["LLM"] = None) -> bool:
        """Summarize old conversation."""
        if len(messages) <= self.KEEP_RECENT:
            return False
        if not llm:
            return False

        old = messages[: -self.KEEP_RECENT]
        tail = messages[-self.KEEP_RECENT :]

        try:
            flat = "\n".join(
                f"[{m.get('role', '?')}] {(m.get('content', '') or '')[:400]}"
                for m in old
            )
            resp = llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "Compress this conversation into a brief summary.",
                    },
                    {"role": "user", "content": flat[:15000]},
                ],
            )
            summary = resp.content
        except Exception:
            return False

        messages.clear()
        messages.append(
            {
                "role": "user",
                "content": f"[Context compressed]\n{summary}",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": "Got it, I have the context.",
            }
        )
        messages.extend(tail)
        return True


class HardCollapseStrategy(CompressionStrategy):
    """Layer 3: Emergency compression."""

    KEEP_TAIL = 4

    def compress(self, messages: list[dict], llm: Optional["LLM"] = None) -> bool:
        """Hard collapse - keep only tail + summary."""
        if len(messages) <= self.KEEP_TAIL:
            return False

        tail = messages[-self.KEEP_TAIL :]
        # For hard collapse, we just drop old messages
        messages.clear()
        messages.append(
            {
                "role": "user",
                "content": "[Hard context reset - older messages dropped]",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": "Context reset. Continuing.",
            }
        )
        messages.extend(tail)
        return True
