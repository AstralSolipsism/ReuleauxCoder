"""Summary generation utilities."""

import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from reuleauxcoder.services.llm.client import LLM


def generate_summary(messages: list[dict], llm: Optional["LLM"] = None) -> str:
    """Generate a summary of messages."""
    if llm:
        try:
            flat = flatten_messages(messages)
            resp = llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Compress this conversation into a brief summary. "
                            "Preserve: file paths edited, key decisions made, "
                            "errors encountered, current task state. "
                            "Drop: verbose command output, code listings."
                        ),
                    },
                    {"role": "user", "content": flat[:15000]},
                ],
            )
            return resp.content
        except Exception:
            pass

    return extract_key_info(messages)


def flatten_messages(messages: list[dict], truncate: int = 400) -> str:
    """Flatten messages to a string."""
    parts = []
    for m in messages:
        role = m.get("role", "?")
        text = m.get("content", "") or ""
        if text:
            parts.append(f"[{role}] {text[:truncate]}")
    return "\n".join(parts)


def extract_key_info(messages: list[dict]) -> str:
    """Extract key information from messages without LLM."""
    files_seen = set()
    errors = []
    decisions = []

    for m in messages:
        text = m.get("content", "") or ""

        # Extract file paths
        for match in re.finditer(r"[\w./\-]+\.\w{1,5}", text):
            files_seen.add(match.group())

        # Extract error lines
        for line in text.splitlines():
            line_lower = line.lower()
            if "error" in line_lower:
                errors.append(line.strip()[:150])
            if "decision" in line_lower or "decided" in line_lower:
                decisions.append(line.strip()[:150])

    parts = []
    if files_seen:
        parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
    if errors:
        parts.append(f"Errors seen: {'; '.join(errors[:5])}")
    if decisions:
        parts.append(f"Decisions: {'; '.join(decisions[:3])}")

    return "\n".join(parts) or "(no extractable context)"
