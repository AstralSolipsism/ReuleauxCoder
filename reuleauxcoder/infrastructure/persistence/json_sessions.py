"""JSON session store - persists sessions to JSON files."""

import json
import time
from pathlib import Path
from typing import Optional, List

from reuleauxcoder.domain.session.models import Session, SessionMetadata
from reuleauxcoder.domain.session.protocols import SessionStoreStore


class JSONSessionStore(SessionStoreStore):
    """Session store that uses JSON files."""

    def __init__(self, sessions_dir: Optional[Path] = None):
        self.sessions_dir = sessions_dir or Path.home() / ".reuleauxcoder" / "sessions"

    def _ensure_dir(self) -> None:
        """Ensure the sessions directory exists."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: Session) -> str:
        """Save a session and return its ID."""
        self._ensure_dir()

        path = self.sessions_dir / f"{session.id}.json"
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2))

        return session.id

    def load(self, session_id: str) -> Optional[Session]:
        """Load a session by ID."""
        path = self.sessions_dir / f"{session_id}.json"

        if not path.exists():
            return None

        data = json.loads(path.read_text())
        return Session.from_dict(data)

    def list(self, limit: int = 20) -> List[SessionMetadata]:
        """List available sessions."""
        if not self.sessions_dir.exists():
            return []

        sessions = []
        for f in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                preview = ""
                for m in data.get("messages", []):
                    if m.get("role") == "user" and m.get("content"):
                        preview = m["content"][:80]
                        break
                sessions.append(
                    SessionMetadata(
                        id=data.get("id", f.stem),
                        model=data.get("model", "?"),
                        saved_at=data.get("saved_at", "?"),
                        preview=preview,
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue

        return sessions[:limit]

    def delete(self, session_id: str) -> bool:
        """Delete a session by ID."""
        path = self.sessions_dir / f"{session_id}.json"

        if path.exists():
            path.unlink()
            return True

        return False
