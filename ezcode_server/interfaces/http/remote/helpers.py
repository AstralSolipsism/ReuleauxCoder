"""Shared helpers for the remote HTTP control plane."""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from typing import Any

GZIP_MIN_BYTES = 1024


def strong_etag(content: bytes) -> str:
    return f'"sha256-{hashlib.sha256(content).hexdigest()}"'


def optional_payload_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def package_version() -> str:
    try:
        return version("reuleauxcoder")
    except PackageNotFoundError:
        return "0.0.0"
