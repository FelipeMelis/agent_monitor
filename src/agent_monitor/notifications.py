"""Deduplicate notifications for sessions that require user attention."""

from __future__ import annotations

from typing import Protocol

from agent_monitor.models import AgentKind, AgentSession, SessionStatus

SessionKey = tuple[AgentKind, str]


class NotificationBackend(Protocol):
    """Deliver a platform notification for one waiting session."""

    def notify(self, session: AgentSession) -> None:
        """Deliver a single notification without transcript content."""


class SoundBackend(Protocol):
    """Play one local sound for a new waiting episode."""

    def play(self) -> None:
        """Play a short sound without session or transcript content."""


class WaitingNotificationManager:
    """Notify once for each continuous waiting episode."""

    def __init__(
        self,
        backend: NotificationBackend,
        *,
        sound_backend: SoundBackend | None = None,
        enabled: bool = True,
        sound_enabled: bool = True,
    ) -> None:
        self.backend = backend
        self.sound_backend = sound_backend
        self.enabled = enabled
        self.sound_enabled = sound_enabled
        self._notified_waits: set[SessionKey] = set()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable delivery for future state refreshes."""

        self.enabled = enabled

    def set_sound_enabled(self, enabled: bool) -> None:
        """Enable or disable sounds for future waiting episodes."""

        self.sound_enabled = enabled

    def update(self, sessions: list[AgentSession]) -> list[AgentSession]:
        """Deliver and return sessions entering a new waiting episode."""

        waiting = {
            _session_key(session): session
            for session in sessions
            if session.status is SessionStatus.WAITING
        }
        waiting_keys = set(waiting)
        self._notified_waits.intersection_update(waiting_keys)

        new_waits = waiting_keys - self._notified_waits
        sorted_waits = [
            waiting[key] for key in sorted(new_waits, key=_sortable_key)
        ]
        if self.enabled:
            for session in sorted_waits:
                self.backend.notify(session)
        if self.sound_enabled and self.sound_backend is not None:
            for _session in sorted_waits:
                self.sound_backend.play()
        self._notified_waits.update(new_waits)
        return sorted_waits


def _session_key(session: AgentSession) -> SessionKey:
    return session.provider, session.session_id


def _sortable_key(key: SessionKey) -> tuple[str, str]:
    return key[0].value, key[1]
