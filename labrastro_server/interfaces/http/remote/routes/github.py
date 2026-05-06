from __future__ import annotations

from http import HTTPStatus
from typing import Any


class RemoteGitHubRoutes:
    def _read_raw_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return b""
        return self.rfile.read(content_length)

    def _handle_github_webhook(self) -> None:
        pr_service = getattr(self.service, "github_pr_service", None)
        if pr_service is None or getattr(self.service, "github_webhook_service", None) is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "github_disabled"})
            return
        body = self._read_raw_body()
        headers = {key: value for key, value in self.headers.items()}
        try:
            result = self.service.github_webhook_service.handle(
                body=body, headers=headers
            )
        except PermissionError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid_signature"})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "github_webhook_failed", "message": str(exc)},
            )
            return
        self._send_json(HTTPStatus.OK, result)

    def _handle_admin_github(self, path: str, payload: dict[str, Any]) -> bool:
        pr_service = getattr(self.service, "github_pr_service", None)
        reconcile_service = getattr(self.service, "github_reconcile_service", None)
        if path == "/remote/admin/github/status":
            status = (
                pr_service.status()
                if pr_service is not None
                else {"enabled": False, "api": {"ok": False}}
            )
            self._send_json(HTTPStatus.OK, {"ok": True, "github": status})
            return True
        if pr_service is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "github_disabled"})
            return True
        if path == "/remote/admin/github/prs/retry":
            try:
                result = pr_service.retry_pr_for_task(
                    task_id=str(payload["task_id"])
                    if payload.get("task_id") is not None
                    else None,
                    artifact_id=str(payload["artifact_id"])
                    if payload.get("artifact_id") is not None
                    else None,
                )
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return True
            self._send_json(HTTPStatus.OK, result.to_dict())
            return True
        if path == "/remote/admin/github/prs/reconcile":
            if reconcile_service is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "github_reconcile_unavailable"},
                )
                return True
            results = reconcile_service.reconcile(
                task_id=str(payload["task_id"])
                if payload.get("task_id") is not None
                else None,
                repository=str(payload["repository"])
                if payload.get("repository") is not None
                else None,
                number=int(payload["number"])
                if payload.get("number") is not None
                else None,
            )
            self._send_json(HTTPStatus.OK, {"ok": True, "results": results})
            return True
        return False
