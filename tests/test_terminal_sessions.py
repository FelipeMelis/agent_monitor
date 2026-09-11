"""Tests for privacy-safe provider-to-terminal session mappings."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_monitor.models import AgentKind
from agent_monitor.terminal_sessions import TerminalSessionStore


class TerminalSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = TerminalSessionStore(self.root)

    def test_iterm_mapping_is_recorded_without_hook_content(self) -> None:
        self.store.record(
            AgentKind.CODEX,
            {
                "session_id": "codex-1",
                "cwd": "/Users/example/project",
                "prompt": "private prompt",
                "tool_input": {"command": "private command"},
            },
            environment={
                "TERM_PROGRAM": "iTerm.app",
                "ITERM_SESSION_ID": "w0t1p0:ABC-123",
            },
        )

        terminal_id = self.store.session_id_for(
            AgentKind.CODEX,
            "codex-1",
        )
        serialized = next(self.root.glob("*.json")).read_text()

        self.assertEqual(terminal_id, "iterm:w0t1p0:ABC-123")
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("private command", serialized)

    def test_apple_terminal_mapping_is_recorded_using_the_tty(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            {"session_id": "claude-1", "cwd": "/Users/example/project"},
            environment={"TERM_PROGRAM": "Apple_Terminal"},
            tty="/dev/ttys001",
        )

        terminal_id = self.store.session_id_for(
            AgentKind.CLAUDE,
            "claude-1",
        )

        self.assertEqual(terminal_id, "terminal:/dev/ttys001")

    def test_apple_terminal_without_a_tty_is_ignored(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            {"session_id": "claude-1"},
            environment={"TERM_PROGRAM": "Apple_Terminal"},
            tty="",
        )

        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_unknown_terminal_program_is_ignored(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            {"session_id": "claude-1"},
            environment={"TERM_PROGRAM": "vscode"},
            tty="/dev/ttys002",
        )

        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_mapping_expires_after_seven_days(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            {"session_id": "claude-1"},
            environment={
                "TERM_PROGRAM": "iTerm.app",
                "TERM_SESSION_ID": "iterm-id",
            },
        )

        terminal_id = self.store.session_id_for(
            AgentKind.CLAUDE,
            "claude-1",
            now=datetime.now(UTC) + timedelta(days=8),
        )

        self.assertEqual(terminal_id, "")


if __name__ == "__main__":
    unittest.main()
