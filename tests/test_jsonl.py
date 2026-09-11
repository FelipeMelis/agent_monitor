"""Tests for bounded JSONL parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_monitor.monitors.jsonl import iter_jsonl_head, iter_jsonl_tail


class JSONLTailTests(unittest.TestCase):
    def test_head_preserves_initial_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                '{"type": "session_meta"}\n'
                '{"type": "event"}\n',
                encoding="utf-8",
            )

            values = list(iter_jsonl_head(path, max_lines=1))

        self.assertEqual(values, [{"type": "session_meta"}])

    def test_invalid_lines_are_ignored_and_tail_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                '{"number": 1}\nnot-json\n{"number": 2}\n',
                encoding="utf-8",
            )

            values = list(iter_jsonl_tail(path, max_lines=2))

        self.assertEqual(values, [{"number": 2}])


if __name__ == "__main__":
    unittest.main()
