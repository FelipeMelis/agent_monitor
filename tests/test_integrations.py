"""Tests for passive hook configuration previews."""

from __future__ import annotations

import unittest
from pathlib import Path

from agent_monitor.integrations import hook_config
from agent_monitor.models import AgentKind


class HookConfigTests(unittest.TestCase):
    def test_claude_config_observes_live_attention(self) -> None:
        config = hook_config(
            AgentKind.CLAUDE,
            executable=Path("/tmp/agent-monitor-hook"),
        )

        hooks = config["hooks"]
        self.assertIn("PermissionRequest", hooks)
        self.assertIn("SessionStart", hooks)
        self.assertIn("Notification", hooks)
        self.assertIn("PermissionDenied", hooks)
        self.assertNotIn("behavior", str(config))

    def test_codex_config_uses_supported_lifecycle_events(self) -> None:
        config = hook_config(
            AgentKind.CODEX,
            executable=Path("/tmp/agent-monitor-hook"),
        )

        hooks = config["hooks"]
        self.assertIn("PermissionRequest", hooks)
        self.assertIn("SessionStart", hooks)
        self.assertIn("PostToolUse", hooks)
        self.assertIn("SessionEnd", hooks)
        self.assertNotIn("Notification", hooks)
        command = hooks["PermissionRequest"][0]["hooks"][0]["command"]
        self.assertEqual(
            command,
            "/tmp/agent-monitor-hook codex permission",
        )
