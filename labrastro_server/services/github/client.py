"""Small GitHub REST client used by the PR lifecycle service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from reuleauxcoder.domain.config.models import GitHubConfig


Transport = Callable[[str, str, dict[str, str], bytes | None], Any]


@dataclass
class GitHubAPIError(RuntimeError):
    status: int
    message: str
    payload: Any = None

    def __str__(self) -> str:
        return f"GitHub API {self.status}: {self.message}"


class GitHubClient:
    def __init__(
        self,
        config: GitHubConfig,
        *,
        token_provider: Callable[[], str],
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self.token_provider = token_provider
        self.transport = transport or self._urllib_transport

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/installation/repositories")

    def find_pull_request(
        self, owner: str, repo: str, *, head: str, state: str = "open"
    ) -> dict[str, Any] | None:
        query = urlencode({"head": f"{owner}:{head}", "state": state})
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls?{query}")
        if isinstance(data, list) and data:
            return dict(data[0])
        return None

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            {
                "head": head,
                "base": base,
                "title": title,
                "body": body,
            },
        )

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def list_reviews(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews")
        return list(data) if isinstance(data, list) else []

    def list_review_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments")
        return list(data) if isinstance(data, list) else []

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token_provider()}",
            "Content-Type": "application/json",
            "User-Agent": "Labrastro-GitHub-App",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        return self.transport(method, self._url(path), headers, body)

    def _url(self, path: str) -> str:
        return self.config.api_base_url.rstrip("/") + "/" + path.lstrip("/")

    @staticmethod
    def _urllib_transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Any:
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=20) as response:  # noqa: S310
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:  # pragma: no cover - network errors are integration-tested.
            status = int(getattr(exc, "code", 0) or 0)
            raw = getattr(exc, "read", lambda: b"")()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                payload = raw.decode("utf-8", errors="replace") if raw else None
            message = (
                str(payload.get("message"))
                if isinstance(payload, dict) and payload.get("message")
                else str(exc)
            )
            raise GitHubAPIError(status, message, payload) from exc
