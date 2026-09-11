"""Base class for bounded JSONL session discovery."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_monitor.models import AgentSession


class JSONLMonitor(ABC):
    """Discover and parse the most recently modified transcript files."""

    def __init__(
        self,
        root: Path,
        *,
        max_sessions: int = 12,
        discovery_window: timedelta = timedelta(hours=24),
    ) -> None:
        self.root = root.expanduser()
        self.max_sessions = max_sessions
        self.discovery_window = discovery_window

    def discover(self) -> list[AgentSession]:
        """Return recent sessions, ignoring unreadable transcripts."""

        if not self.root.is_dir():
            return []

        cutoff = datetime.now(UTC) - self.discovery_window
        candidates: list[tuple[float, Path]] = []
        for path in self.transcript_paths():
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if datetime.fromtimestamp(modified, tz=UTC) >= cutoff:
                candidates.append((modified, path))

        candidates.sort(reverse=True)
        sessions: list[AgentSession] = []
        for _, path in candidates[: self.max_sessions]:
            try:
                sessions.append(self.parse_session(path))
            except (OSError, ValueError):
                continue
        return sessions

    def transcript_paths(self) -> list[Path]:
        """Return provider transcript paths under the configured root."""

        return list(self.root.rglob("*.jsonl"))

    @abstractmethod
    def parse_session(self, path: Path) -> AgentSession:
        """Parse one transcript into the shared session model."""
