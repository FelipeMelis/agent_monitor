"""Tests for Claude Code transcript state detection."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_monitor.models import (
    ActivityKind,
    AgentKind,
    AttentionKind,
    SessionStatus,
)
from agent_monitor.monitors.claude import ClaudeMonitor


class ClaudeMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.project = self.root / "-Users-example-demo"
        self.project.mkdir()

    def write_session(self, *records: dict[str, object]) -> Path:
        path = self.project / "claude-session.jsonl"
        with path.open("w", encoding="utf-8") as transcript:
            for record in records:
                transcript.write(json.dumps(record) + "\n")
        return path

    def test_running_tool_is_reported(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "cwd": "/Users/example/demo",
                "message": {
                    "model": "claude-sonnet",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                        }
                    ],
                },
            }
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.provider, AgentKind.CLAUDE)
        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertEqual(session.project, "demo")
        self.assertEqual(session.action, "Read")
        self.assertEqual(session.activity, ActivityKind.RUNNING_TOOL)
        self.assertEqual(
            session.status_reason,
            "a tool call is awaiting its result",
        )

    def test_tool_result_and_end_turn_are_complete(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                        }
                    ]
                },
            },
            {
                "timestamp": now,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                        }
                    ]
                },
            },
            {
                "timestamp": now,
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Done"}],
                },
            },
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.COMPLETE)
        self.assertEqual(session.action, "Finished")
        self.assertEqual(session.activity, ActivityKind.FINISHED)

    def test_explicit_permission_event_is_waiting(self) -> None:
        path = self.write_session(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "type": "permission_request",
                "cwd": "/Users/example/demo",
                "tool_name": "Edit",
                "tool_input": {"file_path": "src/app.py"},
            }
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.WAITING)
        self.assertEqual(session.activity, ActivityKind.WAITING_APPROVAL)
        self.assertEqual(session.attention_kind, AttentionKind.EDIT)
        self.assertEqual(session.attention_summary, "src/app.py")

    def test_active_edit_without_permission_event_is_running(self) -> None:
        active_time = datetime.now(UTC) - timedelta(seconds=6)
        path = self.write_session(
            {
                "timestamp": active_time.isoformat(),
                "cwd": "/Users/example/demo",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Edit",
                            "input": {"file_path": "src/app.py"},
                        }
                    ]
                },
            }
        )
        timestamp = active_time.timestamp()
        os.utime(path, (timestamp, timestamp))

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertEqual(session.activity, ActivityKind.RUNNING_TOOL)
        self.assertEqual(
            session.status_reason,
            "a tool call is awaiting its result",
        )

    def test_permission_mode_metadata_is_not_a_wait_request(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "assistant",
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Done"}],
                },
            },
            {
                "type": "permission-mode",
                "permissionMode": "default",
            },
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.COMPLETE)

    def test_user_activity_clears_an_explicit_wait(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "permission_request",
            },
            {
                "timestamp": now,
                "type": "user",
                "message": {"role": "user", "content": "approved"},
            },
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.RUNNING)

    def test_stale_permission_event_becomes_idle(self) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        path = self.write_session(
            {
                "timestamp": old_time.isoformat(),
                "type": "permission_request",
            }
        )
        timestamp = old_time.timestamp()
        os.utime(path, (timestamp, timestamp))

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.IDLE)

    def test_thinking_after_permission_clears_waiting(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "permission_request",
            },
            {
                "timestamp": now,
                "message": {
                    "content": [{"type": "thinking"}],
                },
            },
        )

        session = ClaudeMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertEqual(session.activity, ActivityKind.THINKING)

    def test_subagent_transcripts_are_not_discovered(self) -> None:
        self.write_session(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "message": {"stop_reason": "end_turn"},
            }
        )
        subagents = self.project / "session" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent.jsonl").write_text("{}\n", encoding="utf-8")

        sessions = ClaudeMonitor(self.root).discover()

        self.assertEqual(len(sessions), 1)


if __name__ == "__main__":
    unittest.main()
