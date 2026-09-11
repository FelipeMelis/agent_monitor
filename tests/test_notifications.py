"""Tests for deduplicated waiting-session notifications."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agent_monitor.models import AgentKind, AgentSession, SessionStatus
from agent_monitor.notifications import WaitingNotificationManager


class RecordingBackend:
    def __init__(self) -> None:
        self.sessions: list[AgentSession] = []

    def notify(self, session: AgentSession) -> None:
        self.sessions.append(session)


class RecordingSoundBackend:
    def __init__(self) -> None:
        self.play_count = 0

    def play(self) -> None:
        self.play_count += 1


def make_session(
    session_id: str,
    status: SessionStatus = SessionStatus.WAITING,
) -> AgentSession:
    return AgentSession(
        session_id=session_id,
        provider=AgentKind.CODEX,
        status=status,
        project=session_id,
        last_activity=datetime.now(UTC),
        transcript_path=Path(f"{session_id}.jsonl"),
    )


class WaitingNotificationManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = RecordingBackend()
        self.sound_backend = RecordingSoundBackend()
        self.manager = WaitingNotificationManager(
            self.backend,
            sound_backend=self.sound_backend,
        )

    def test_continuous_wait_notifies_only_once(self) -> None:
        waiting = make_session("one")

        first = self.manager.update([waiting])
        second = self.manager.update([waiting])

        self.assertEqual(self.backend.sessions, [waiting])
        self.assertEqual(first, [waiting])
        self.assertEqual(second, [])
        self.assertEqual(self.sound_backend.play_count, 1)

    def test_new_wait_after_resolution_notifies_again(self) -> None:
        waiting = make_session("one")
        running = replace(waiting, status=SessionStatus.RUNNING)

        self.manager.update([waiting])
        self.manager.update([running])
        self.manager.update([waiting])

        self.assertEqual(self.backend.sessions, [waiting, waiting])
        self.assertEqual(self.sound_backend.play_count, 2)

    def test_disappeared_session_can_notify_if_it_returns(self) -> None:
        waiting = make_session("one")

        self.manager.update([waiting])
        self.manager.update([])
        self.manager.update([waiting])

        self.assertEqual(self.backend.sessions, [waiting, waiting])

    def test_disabled_mode_suppresses_current_wait(self) -> None:
        waiting = make_session("one")
        self.manager.set_enabled(False)

        new_waits = self.manager.update([waiting])
        self.manager.set_enabled(True)
        self.manager.update([waiting])

        self.assertEqual(self.backend.sessions, [])
        self.assertEqual(new_waits, [waiting])
        self.assertEqual(self.sound_backend.play_count, 1)

    def test_future_wait_notifies_after_reenabling(self) -> None:
        first = make_session("one")
        second = make_session("two")
        self.manager.set_enabled(False)
        self.manager.update([first])
        self.manager.set_enabled(True)

        self.manager.update([first, second])

        self.assertEqual(self.backend.sessions, [second])
        self.assertEqual(self.sound_backend.play_count, 2)

    def test_disabled_sound_does_not_affect_visual_notification(self) -> None:
        waiting = make_session("one")
        self.manager.set_sound_enabled(False)

        self.manager.update([waiting])

        self.assertEqual(self.backend.sessions, [waiting])
        self.assertEqual(self.sound_backend.play_count, 0)

    def test_sound_reenable_applies_only_to_future_waits(self) -> None:
        first = make_session("one")
        second = make_session("two")
        self.manager.set_sound_enabled(False)
        self.manager.update([first])
        self.manager.set_sound_enabled(True)

        self.manager.update([first, second])

        self.assertEqual(self.sound_backend.play_count, 1)


if __name__ == "__main__":
    unittest.main()
