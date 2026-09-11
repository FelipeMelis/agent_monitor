"""Tests for sanitized provider-hook attention events."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_monitor.live_events import LiveEventStore
from agent_monitor.models import AgentKind, AttentionKind


class LiveEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = LiveEventStore(self.root)

    def test_claude_permission_is_sanitized(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            "permission",
            {
                "session_id": "claude-1",
                "cwd": "/Users/example/project",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "private.py",
                    "new_string": "secret source",
                },
            },
        )

        events = self.store.active_events()
        serialized = next(self.root.glob("*.json")).read_text()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].provider, AgentKind.CLAUDE)
        self.assertEqual(events[0].project, "project")
        self.assertEqual(events[0].kind, AttentionKind.EDIT)
        self.assertNotIn("private.py", serialized)
        self.assertNotIn("secret source", serialized)

    def test_codex_camel_case_payload_is_normalized(self) -> None:
        self.store.record(
            AgentKind.CODEX,
            "permission",
            {
                "threadId": "codex-1",
                "cwd": "/Users/example/api",
                "toolName": "bash",
            },
        )

        event = self.store.active_events()[0]

        self.assertEqual(event.session_id, "codex-1")
        self.assertEqual(event.project, "api")
        self.assertEqual(event.kind, AttentionKind.COMMAND)
        self.assertEqual(event.summary, "Codex requests bash access")

    def test_clear_event_removes_waiting_state(self) -> None:
        payload = {
            "sessionId": "codex-1",
            "cwd": "/Users/example/api",
            "toolName": "bash",
        }
        self.store.record(AgentKind.CODEX, "permission", payload)

        self.store.record(AgentKind.CODEX, "post_tool", payload)

        self.assertEqual(self.store.active_events(), [])

    def test_unrelated_notification_is_ignored(self) -> None:
        self.store.record(
            AgentKind.CLAUDE,
            "notification",
            {
                "session_id": "claude-1",
                "notification_type": "auth_success",
            },
        )

        self.assertEqual(self.store.active_events(), [])

    def test_claude_idle_prompt_clears_attention(self) -> None:
        payload = {
            "session_id": "claude-1",
            "cwd": "/Users/example/project",
        }
        self.store.record(AgentKind.CLAUDE, "permission", payload)

        self.store.record(
            AgentKind.CLAUDE,
            "notification",
            {**payload, "notification_type": "idle_prompt"},
        )

        self.assertEqual(self.store.active_events(), [])
