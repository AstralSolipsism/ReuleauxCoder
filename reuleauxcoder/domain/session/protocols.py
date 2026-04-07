"""Session domain protocols - abstract interfaces."""

from typing import Protocol, Optional, List
from reuleauxcoder.domain.session.models import Session, SessionMetadata


class SessionStoreProtocol(Protocol):
    """Protocol for session storage implementations."""

    def save(self, session: Session) -> str:
        """Save a session and return its ID."""
        ...

    def load(self, session_id: str) -> Optional[Session]:
        """Load a session by ID."""
        ...

    def list(self, limit: int = 20) -> List[SessionMetadata]:
        """List available sessions."""
        ...
