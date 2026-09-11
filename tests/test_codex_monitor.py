"""Tests for Codex rollout state detection."""

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
from agent_monitor.monitors.codex import CodexMonitor


class CodexMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.root.mkdir(exist_ok=True)

    def write_session(self, *records: dict[str, object]) -> Path:
        path = self.root / "rollout-session.jsonl"
        with path.open("w", encoding="utf-8") as transcript:
            for record in records:
                transcript.write(json.dumps(record) + "\n")
        return path

    def test_approval_request_is_waiting(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "session_meta",
                "payload": {
                    "type": "session_meta",
                    "id": "codex-123",
                    "cwd": "/Users/example/api",
                },
            },
            {
                "timestamp": now,
                "type": "event_msg",
                "payload": {
                    "type": "exec_approval_request",
                    "command": "npm test",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.provider, AgentKind.CODEX)
        self.assertEqual(session.status, SessionStatus.WAITING)
        self.assertEqual(session.activity, ActivityKind.WAITING_APPROVAL)
        self.assertEqual(session.project, "api")
        self.assertEqual(session.action, "npm test")
        self.assertEqual(session.attention_kind, AttentionKind.COMMAND)
        self.assertEqual(session.attention_summary, "npm test")
        self.assertEqual(
            session.status_reason,
            "an approval or user-input event is unresolved",
        )

    def test_patch_approval_describes_file_change(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "event_msg",
                "payload": {
                    "type": "apply_patch_approval_request",
                    "file_path": "src/app.py",
                },
            }
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.attention_kind, AttentionKind.EDIT)
        self.assertEqual(session.attention_summary, "src/app.py")

    def test_user_input_request_describes_question(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "event_msg",
                "payload": {
                    "type": "request_user_input",
                    "questions": [{"question": "Deploy now?"}],
                },
            }
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.attention_kind, AttentionKind.INPUT)
        self.assertEqual(session.attention_summary, "Deploy now?")

    def test_completed_function_call_is_complete(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": "Done",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.COMPLETE)
        self.assertEqual(session.action, "Finished")

    def test_stale_active_call_becomes_idle(self) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        path = self.write_session(
            {
                "timestamp": old_time.isoformat(),
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                },
            }
        )
        timestamp = old_time.timestamp()
        os.utime(path, (timestamp, timestamp))

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.IDLE)

    def test_custom_tool_call_after_message_is_running(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call-1",
                    "name": "exec",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertEqual(session.action, "exec")
        self.assertEqual(session.activity, ActivityKind.RUNNING_COMMAND)

    def test_custom_tool_escalation_is_waiting(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call-1",
                    "name": "exec",
                    "input": (
                        "tools.exec_command({"
                        'sandbox_permissions: "require_escalated"'
                        "})"
                    ),
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.WAITING)
        self.assertEqual(session.action, "exec")
        self.assertEqual(session.attention_kind, AttentionKind.COMMAND)
        self.assertEqual(session.attention_summary, "Review command")

    def test_escalation_output_clears_waiting(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {"sandbox_permissions": "require_escalated"}
                    ),
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertNotEqual(session.status, SessionStatus.WAITING)
        self.assertEqual(session.activity, ActivityKind.PROCESSING_RESULT)
        self.assertIsNone(session.attention_kind)
        self.assertEqual(session.attention_summary, "")

    def test_yielded_command_is_reported_as_running(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {"sandbox_permissions": "require_escalated"}
                    ),
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "Script running with cell ID 123",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.RUNNING)
        self.assertEqual(session.activity, ActivityKind.RUNNING_COMMAND)
        self.assertIn("still running", session.status_reason)
        self.assertIsNone(session.attention_kind)

    def test_final_poll_clears_background_command(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "Script running with cell ID 123",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-2",
                    "name": "write_stdin",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-2",
                    "output": "Process exited with code 0",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.activity, ActivityKind.PROCESSING_RESULT)
        self.assertNotIn("still running", session.status_reason)

    def test_finished_message_clears_background_command(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "exec_command",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "Script running with cell ID 123",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": "Done",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.status, SessionStatus.COMPLETE)
        self.assertEqual(session.activity, ActivityKind.FINISHED)

    def test_marker_text_from_non_command_tool_is_ignored(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.write_session(
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "read_file",
                },
            },
            {
                "timestamp": now,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "Script running with cell ID in documentation",
                },
            },
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.activity, ActivityKind.PROCESSING_RESULT)
        self.assertNotIn("still running", session.status_reason)

    def test_head_metadata_survives_a_large_transcript(self) -> None:
        now = datetime.now(UTC).isoformat()
        path = self.project_path()
        metadata = {
            "timestamp": now,
            "type": "session_meta",
            "payload": {
                "type": "session_meta",
                "id": "codex-large",
                "cwd": "/Users/example/large-project",
            },
        }
        event = {
            "timestamp": now,
            "type": "response_item",
            "payload": {"type": "reasoning"},
        }
        path.write_text(
            json.dumps(metadata)
            + "\n"
            + json.dumps({"padding": "x" * 1_100_000})
            + "\n"
            + json.dumps(event)
            + "\n",
            encoding="utf-8",
        )

        session = CodexMonitor(self.root).parse_session(path)

        self.assertEqual(session.session_id, "codex-large")
        self.assertEqual(session.project, "large-project")

    def project_path(self) -> Path:
        return self.root / "rollout-session.jsonl"


if __name__ == "__main__":
    unittest.main()
