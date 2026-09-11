"""Tests for the fail-open provider hook bridge."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_monitor.hook_bridge import main


class HookBridgeTests(unittest.TestCase):
    def test_session_start_records_only_terminal_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            event_root = root / "events"
            terminal_root = root / "terminal"
            payload = {
                "session_id": "codex-1",
                "cwd": "/Users/example/project",
                "prompt": "private prompt",
            }
            environment = {
                "AGENT_MONITOR_EVENT_DIR": str(event_root),
                "AGENT_MONITOR_TERMINAL_DIR": str(terminal_root),
                "TERM_PROGRAM": "iTerm.app",
                "ITERM_SESSION_ID": "w0t1p0:ABC-123",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("sys.stdin", io.StringIO(json.dumps(payload))),
            ):
                main(["codex", "terminal_session"])

            mappings = list(terminal_root.glob("*.json"))

            self.assertEqual(len(mappings), 1)
            self.assertFalse(event_root.exists())
            self.assertNotIn(
                "private prompt",
                mappings[0].read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
