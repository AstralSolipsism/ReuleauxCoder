"""Remote relay protocol models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class ErrorMessage:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ErrorMessage":
        return cls(code=d["code"], message=d.get("message", ""))

__all__ = [
    "ErrorMessage",
]
