"""Safe worktree planning helpers for daemon-owned Agent runtime roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_branch_segment(value: str, *, fallback: str = "agent") -> str:
    """Return a git-branch-safe segment without path traversal semantics."""

    text = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip(".-_/\\")
    return text[:64] if text else fallback


@dataclass(frozen=True)
class WorktreePlan:
    """A side-effect-free description of one daemon-owned worktree."""

    runtime_root: Path
    workspace_id: str
    task_id: str
    agent_id: str
    branch_name: str
    worktree_path: Path
    cache_path: Path | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "runtime_root": str(self.runtime_root),
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "branch_name": self.branch_name,
            "worktree_path": str(self.worktree_path),
            "cache_path": str(self.cache_path) if self.cache_path else None,
        }


class WorktreeOwnershipError(ValueError):
    """Raised when code tries to operate outside the daemon-owned runtime root."""


class WorktreeManager:
    """Build worktree plans constrained to a single daemon-owned root."""

    def __init__(self, runtime_root: str | Path) -> None:
        root = Path(runtime_root).expanduser()
        if not str(root).strip():
            raise ValueError("runtime root is required")
        self.runtime_root = root.resolve()

    def plan(
        self,
        *,
        workspace_id: str,
        task_id: str,
        agent_id: str,
        repo_url: str | None = None,
        branch_name: str | None = None,
    ) -> WorktreePlan:
        workspace = sanitize_branch_segment(workspace_id, fallback="workspace")
        task = sanitize_branch_segment(task_id, fallback="task")
        agent = sanitize_branch_segment(agent_id, fallback="agent")
        branch = branch_name or f"agent/{agent}/{task[:12]}"
        worktree_path = (
            self.runtime_root / "worktrees" / workspace / f"{agent}-{task[:12]}"
        ).resolve()
        self.assert_owned(worktree_path)
        cache_path = None
        if repo_url:
            cache_path = (self.runtime_root / "repos" / workspace / _repo_cache_name(repo_url)).resolve()
            self.assert_owned(cache_path)
        return WorktreePlan(
            runtime_root=self.runtime_root,
            workspace_id=workspace,
            task_id=task,
            agent_id=agent,
            branch_name=branch,
            worktree_path=worktree_path,
            cache_path=cache_path,
        )

    def assert_owned(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.runtime_root)
        except ValueError as exc:
            raise WorktreeOwnershipError(
                f"path is outside agent runtime root: {resolved}"
            ) from exc
        return resolved


def _repo_cache_name(repo_url: str) -> str:
    text = repo_url.strip().rstrip("/")
    if not text:
        return "repo.git"
    text = text.replace(":", "+").replace("@", "+").replace("\\", "/")
    parts = [sanitize_branch_segment(part, fallback="repo") for part in text.split("/")]
    name = "+".join(part for part in parts if part)
    if not name.endswith(".git"):
        name += ".git"
    return name


__all__ = [
    "WorktreeManager",
    "WorktreeOwnershipError",
    "WorktreePlan",
    "sanitize_branch_segment",
]
