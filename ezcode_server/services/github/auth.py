"""GitHub App authentication helpers."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable
from urllib.request import Request, urlopen

from reuleauxcoder.domain.config.models import GitHubConfig


class GitHubAppAuth:
    """Create GitHub App JWTs from an unencrypted RSA private key file."""

    def __init__(self, config: GitHubConfig) -> None:
        self.config = config

    def app_jwt(self, *, now: int | None = None) -> str:
        if not self.config.app_id:
            raise RuntimeError("github.app_id is required")
        key_path = Path(self.config.private_key_path).expanduser()
        if not key_path.is_file():
            raise RuntimeError(f"GitHub App private key not found: {key_path}")
        pem = key_path.read_text(encoding="utf-8")
        issued_at = int(now if now is not None else time.time()) - 60
        payload = {
            "iat": issued_at,
            "exp": issued_at + 600,
            "iss": self.config.app_id,
        }
        return _jwt_rs256(payload, pem)

    def installation_token_path(self) -> str:
        if not self.config.installation_id:
            raise RuntimeError("github.installation_id is required")
        return f"/app/installations/{self.config.installation_id}/access_tokens"


class GitHubInstallationTokenProvider:
    """Cache and refresh installation access tokens for the configured GitHub App."""

    def __init__(
        self,
        config: GitHubConfig,
        *,
        auth: GitHubAppAuth | None = None,
        transport: Callable[[str, str, dict[str, str], bytes | None], Any] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.auth = auth or GitHubAppAuth(config)
        self.transport = transport or self._urllib_transport
        self.now = now
        self._token = ""
        self._expires_at = 0.0

    def __call__(self) -> str:
        if self._token and self.now() < self._expires_at - 60:
            return self._token
        payload = self._request_token()
        token = str(payload.get("token") or "")
        if not token:
            raise RuntimeError("GitHub installation token response missing token")
        self._token = token
        self._expires_at = _parse_github_expiry(payload.get("expires_at"), self.now())
        return self._token

    def _request_token(self) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.auth.app_jwt()}",
            "Content-Type": "application/json",
            "User-Agent": "EZCode-GitHub-App",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = self.transport(
            "POST",
            self.config.api_base_url.rstrip("/") + self.auth.installation_token_path(),
            headers,
            b"{}",
        )
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _urllib_transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Any:
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=20) as response:  # noqa: S310
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}


def _parse_github_expiry(value: Any, fallback_now: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return fallback_now + 3600
    try:
        from datetime import datetime

        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return fallback_now + 3600


def _jwt_rs256(payload: dict[str, object], pem: str) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url_json(header),
            _b64url_json(payload),
        ]
    ).encode("ascii")
    signature = _rsa_sha256_sign(signing_input, pem)
    return signing_input.decode("ascii") + "." + _b64url(signature)


def _b64url_json(value: dict[str, object]) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _rsa_sha256_sign(data: bytes, pem: str) -> bytes:
    n, d = _rsa_private_numbers(pem)
    digest = hashlib.sha256(data).digest()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    key_len = (n.bit_length() + 7) // 8
    if len(digest_info) > key_len - 11:
        raise RuntimeError("RSA key is too small for RS256")
    encoded = b"\x00\x01" + b"\xff" * (key_len - len(digest_info) - 3) + b"\x00" + digest_info
    signed = pow(int.from_bytes(encoded, "big"), d, n)
    return signed.to_bytes(key_len, "big")


def _rsa_private_numbers(pem: str) -> tuple[int, int]:
    der = _pem_to_der(pem)
    reader = _DERReader(der)
    seq = reader.sequence()
    first = seq.integer()
    if first == 0 and seq.peek_tag() == 0x02:
        n = seq.integer()
        _e = seq.integer()
        d = seq.integer()
        return n, d
    seq = _DERReader(der).sequence()
    _version = seq.integer()
    _algorithm = seq.tlv()
    private_key = seq.octet_string()
    inner = _DERReader(private_key).sequence()
    _rsa_version = inner.integer()
    n = inner.integer()
    _e = inner.integer()
    d = inner.integer()
    return n, d


def _pem_to_der(pem: str) -> bytes:
    lines = [
        line.strip()
        for line in pem.splitlines()
        if line.strip() and not line.startswith("-----")
    ]
    if not lines:
        raise RuntimeError("GitHub App private key PEM is empty")
    return base64.b64decode("".join(lines))


class _DERReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def peek_tag(self) -> int | None:
        return self.data[self.pos] if self.pos < len(self.data) else None

    def sequence(self) -> "_DERReader":
        tag, value = self.tlv()
        if tag != 0x30:
            raise RuntimeError("expected ASN.1 SEQUENCE")
        return _DERReader(value)

    def integer(self) -> int:
        tag, value = self.tlv()
        if tag != 0x02:
            raise RuntimeError("expected ASN.1 INTEGER")
        return int.from_bytes(value.lstrip(b"\x00") or b"\x00", "big")

    def octet_string(self) -> bytes:
        tag, value = self.tlv()
        if tag != 0x04:
            raise RuntimeError("expected ASN.1 OCTET STRING")
        return value

    def tlv(self) -> tuple[int, bytes]:
        if self.pos >= len(self.data):
            raise RuntimeError("truncated ASN.1 data")
        tag = self.data[self.pos]
        self.pos += 1
        if self.pos >= len(self.data):
            raise RuntimeError("truncated ASN.1 length")
        length = self.data[self.pos]
        self.pos += 1
        if length & 0x80:
            size = length & 0x7F
            if size == 0 or self.pos + size > len(self.data):
                raise RuntimeError("invalid ASN.1 length")
            length = int.from_bytes(self.data[self.pos : self.pos + size], "big")
            self.pos += size
        end = self.pos + length
        if end > len(self.data):
            raise RuntimeError("truncated ASN.1 value")
        value = self.data[self.pos : end]
        self.pos = end
        return tag, value
