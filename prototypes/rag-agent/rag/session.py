"""Session memory management for RAG chat."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600  # 1 hour
MAX_MESSAGES_PER_SESSION = 50
HISTORY_WINDOW = 10  # Number of recent messages to include in context


@dataclass
class ChatMessage:
    """A single chat message."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Session:
    """A chat session with message history."""

    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the session history."""
        self.messages.append(ChatMessage(role=role, content=content))
        self.last_accessed = time.time()

        # Trim if exceeds max
        if len(self.messages) > MAX_MESSAGES_PER_SESSION:
            self.messages = self.messages[-MAX_MESSAGES_PER_SESSION:]

    def get_history(self, window: int = HISTORY_WINDOW) -> list[dict[str, str]]:
        """Get recent message history for context.

        Returns:
            List of dicts with 'role' and 'content' keys.
        """
        recent = self.messages[-window:] if len(self.messages) > window else self.messages
        return [{"role": m.role, "content": m.content} for m in recent]


class SessionMemory:
    """In-memory session storage with TTL-based cleanup."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        """Initialize session memory.

        Args:
            ttl_seconds: Time-to-live for sessions in seconds.
        """
        self._sessions: dict[str, Session] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        logger.info(
            "SessionMemory initialized: ttl=%ds, max_messages=%d",
            ttl_seconds,
            MAX_MESSAGES_PER_SESSION,
        )

    def get_or_create(self, session_id: str | None = None) -> Session:
        """Get an existing session or create a new one.

        Args:
            session_id: Optional session ID. If None, creates new session.

        Returns:
            Session instance.
        """
        with self._lock:
            # Cleanup expired sessions periodically
            self._cleanup_expired()

            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_accessed = time.time()
                logger.debug(
                    "Retrieved session: %s (messages=%d)",
                    session_id,
                    len(session.messages),
                )
                return session

            # Create new session
            new_id = session_id or str(uuid.uuid4())
            session = Session(session_id=new_id)
            self._sessions[new_id] = session
            logger.info("Created new session: %s", new_id)
            return session

    def add_message(
        self, session_id: str, role: str, content: str
    ) -> None:
        """Add a message to a session.

        Args:
            session_id: The session ID.
            role: Message role ("user" or "assistant").
            content: Message content.
        """
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].add_message(role, content)
                logger.debug(
                    "Added message to session %s: role=%s, length=%d",
                    session_id,
                    role,
                    len(content),
                )

    def get_history(
        self, session_id: str, window: int = HISTORY_WINDOW
    ) -> list[dict[str, str]]:
        """Get message history for a session.

        Args:
            session_id: The session ID.
            window: Number of recent messages to return.

        Returns:
            List of message dicts.
        """
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id].get_history(window)
            return []

    def _cleanup_expired(self) -> None:
        """Remove expired sessions."""
        current_time = time.time()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if current_time - session.last_accessed > self._ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]
            logger.info("Expired session removed: %s", sid)

    def clear(self) -> None:
        """Clear all sessions. Used for testing."""
        with self._lock:
            self._sessions.clear()
            logger.debug("All sessions cleared")

    @property
    def session_count(self) -> int:
        """Return the number of active sessions."""
        with self._lock:
            return len(self._sessions)


# Global session memory instance
_session_memory: SessionMemory | None = None


def get_session_memory() -> SessionMemory:
    """Get the global session memory instance."""
    global _session_memory
    if _session_memory is None:
        _session_memory = SessionMemory()
    return _session_memory


def reset_session_memory() -> None:
    """Reset the global session memory. Used for testing."""
    global _session_memory
    if _session_memory:
        _session_memory.clear()
    _session_memory = None
