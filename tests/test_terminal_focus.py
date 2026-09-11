"""Tests for focusing mapped terminal sessions (iTerm2 and Terminal.app)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agent_monitor.models import AgentKind, AgentSession, SessionStatus
from agent_monitor.terminal_focus import TerminalFocuser


def make_session() -> AgentSession:
    return AgentSession(
        session_id="session-1",
        provider=AgentKind.CODEX,
        status=SessionStatus.RUNNING,
        project="project",
        last_activity=datetime.now(UTC),
        transcript_path=Path("session.jsonl"),
    )


class TerminalFocuserTests(unittest.TestCase):
    def test_iterm_mapping_is_routed_to_the_iterm_backend(self) -> None:
        focused_ids: list[str] = []
        terminal_calls: list[str] = []
        focuser = TerminalFocuser(
            iterm_backend=lambda raw_id: not focused_ids.append(raw_id),
            apple_terminal_backend=lambda raw_id: not terminal_calls.append(
                raw_id
            ),
        )
        session = replace(
            make_session(),
            terminal_session_id="iterm:w0t1p0:ABC/123",
        )

        focused = focuser.focus(session)

        self.assertTrue(focused)
        self.assertEqual(focused_ids, ["w0t1p0:ABC/123"])
        self.assertEqual(terminal_calls, [])

    def test_apple_terminal_mapping_is_routed_to_that_backend(self) -> None:
        focused_ttys: list[str] = []
        iterm_calls: list[str] = []
        focuser = TerminalFocuser(
            iterm_backend=lambda raw_id: not iterm_calls.append(raw_id),
            apple_terminal_backend=lambda tty: not focused_ttys.append(tty),
        )
        session = replace(
            make_session(),
            terminal_session_id="terminal:/dev/ttys001",
        )

        focused = focuser.focus(session)

        self.assertTrue(focused)
        self.assertEqual(focused_ttys, ["/dev/ttys001"])
        self.assertEqual(iterm_calls, [])

    def test_missing_mapping_does_not_call_any_backend(self) -> None:
        calls: list[str] = []
        focuser = TerminalFocuser(
            iterm_backend=lambda raw_id: not calls.append(raw_id),
            apple_terminal_backend=lambda raw_id: not calls.append(raw_id),
        )

        focused = focuser.focus(make_session())

        self.assertFalse(focused)
        self.assertEqual(calls, [])

    def test_unknown_backend_tag_does_not_call_any_backend(self) -> None:
        calls: list[str] = []
        focuser = TerminalFocuser(
            iterm_backend=lambda raw_id: not calls.append(raw_id),
            apple_terminal_backend=lambda raw_id: not calls.append(raw_id),
        )
        session = replace(
            make_session(),
            terminal_session_id="somethingelse:abc",
        )

        focused = focuser.focus(session)

        self.assertFalse(focused)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
