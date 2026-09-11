"""Tests for the privacy-safe debug command output."""

from __future__ import annotations

import io
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agent_monitor.app import _run_debug
from agent_monitor.models import AgentKind, AgentSession, SessionStatus


class StaticCoordinator:
    def __init__(self, sessions: list[AgentSession]) -> None:
        self.sessions = sessions

    def refresh(self) -> list[AgentSession]:
        return self.sessions


class DebugCommandTests(unittest.TestCase):
    def test_one_time_debug_snapshot_explains_session_state(self) -> None:
        session = AgentSession(
            session_id="session-1",
            provider=AgentKind.CODEX,
            status=SessionStatus.RUNNING,
            project="demo",
            last_activity=datetime.now(UTC),
            transcript_path=Path("private-transcript.jsonl"),
            action="exec",
            status_reason="a tool call is awaiting its output",
        )
        output = io.StringIO()

        _run_debug(
            StaticCoordinator([session]),  # type: ignore[arg-type]
            watch=False,
            output=output,
        )

        value = output.getvalue()
        self.assertIn("codex | demo | running", value)
        self.assertIn("activity=active", value)
        self.assertIn("evidence=inferred/transcript", value)
        self.assertIn("action=exec", value)
        self.assertIn(
            "reason=a tool call is awaiting its output",
            value,
        )
        self.assertNotIn("private-transcript.jsonl", value)

    def test_empty_snapshot_is_explicit(self) -> None:
        output = io.StringIO()

        _run_debug(
            StaticCoordinator([]),  # type: ignore[arg-type]
            watch=False,
            output=output,
        )

        self.assertIn("No sessions in the current feed.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
