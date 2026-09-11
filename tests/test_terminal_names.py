"""Tests for looking up current terminal session names."""

from __future__ import annotations

import unittest

from agent_monitor.terminal_names import TerminalNameLookup


class TerminalNameLookupTests(unittest.TestCase):
    def test_maps_iterm_output_back_to_full_terminal_session_ids(
        self,
    ) -> None:
        captured_raw_ids: list[list[str]] = []

        def fake_iterm(raw_ids: list[str]) -> str:
            captured_raw_ids.append(raw_ids)
            return "ABC/123\tmy renamed tab\n"

        lookup = TerminalNameLookup(iterm_backend=fake_iterm)

        names = lookup.names_for(["iterm:w0t1p0:ABC/123"])

        self.assertEqual(names, {"iterm:w0t1p0:ABC/123": "my renamed tab"})
        self.assertEqual(captured_raw_ids, [["ABC/123"]])

    def test_maps_apple_terminal_output_back_to_full_ids(self) -> None:
        captured_raw_ids: list[list[str]] = []

        def fake_terminal(raw_ids: list[str]) -> str:
            captured_raw_ids.append(raw_ids)
            return "/dev/ttys001\tbuild watcher\n"

        lookup = TerminalNameLookup(apple_terminal_backend=fake_terminal)

        names = lookup.names_for(["terminal:/dev/ttys001"])

        self.assertEqual(
            names,
            {"terminal:/dev/ttys001": "build watcher"},
        )
        self.assertEqual(captured_raw_ids, [["/dev/ttys001"]])

    def test_mixed_ids_query_each_backend_independently(self) -> None:
        lookup = TerminalNameLookup(
            iterm_backend=lambda raw_ids: "ABC\tIterm Tab\n",
            apple_terminal_backend=lambda raw_ids: (
                "/dev/ttys001\tTerminal Tab\n"
            ),
        )

        names = lookup.names_for(
            ["iterm:w0t1p0:ABC", "terminal:/dev/ttys001"]
        )

        self.assertEqual(
            names,
            {
                "iterm:w0t1p0:ABC": "Iterm Tab",
                "terminal:/dev/ttys001": "Terminal Tab",
            },
        )

    def test_no_ids_never_calls_either_backend(self) -> None:
        calls: list[list[str]] = []
        lookup = TerminalNameLookup(
            iterm_backend=lambda raw_ids: calls.append(raw_ids) or "",
            apple_terminal_backend=lambda raw_ids: calls.append(raw_ids)
            or "",
        )

        names = lookup.names_for([])

        self.assertEqual(names, {})
        self.assertEqual(calls, [])

    def test_blank_and_unknown_backend_ids_are_ignored(self) -> None:
        calls: list[list[str]] = []
        lookup = TerminalNameLookup(
            iterm_backend=lambda raw_ids: calls.append(raw_ids) or "",
            apple_terminal_backend=lambda raw_ids: calls.append(raw_ids)
            or "",
        )

        names = lookup.names_for(["", "somethingelse:abc"])

        self.assertEqual(names, {})
        self.assertEqual(calls, [])

    def test_empty_backend_output_yields_no_names(self) -> None:
        lookup = TerminalNameLookup(iterm_backend=lambda raw_ids: "")

        names = lookup.names_for(["iterm:w0t1p0:ABC/123"])

        self.assertEqual(names, {})

    def test_lines_without_a_matching_id_are_ignored(self) -> None:
        lookup = TerminalNameLookup(
            iterm_backend=lambda raw_ids: "unknown-id\tsome name\n",
        )

        names = lookup.names_for(["iterm:w0t1p0:ABC/123"])

        self.assertEqual(names, {})

    def test_duplicate_terminal_ids_are_deduplicated(self) -> None:
        captured_raw_ids: list[list[str]] = []

        def fake_iterm(raw_ids: list[str]) -> str:
            captured_raw_ids.append(raw_ids)
            return "ABC/123\tone\n"

        lookup = TerminalNameLookup(iterm_backend=fake_iterm)

        lookup.names_for(
            ["iterm:w0t1p0:ABC/123", "iterm:w0t1p0:ABC/123"]
        )

        self.assertEqual(captured_raw_ids, [["ABC/123"]])


if __name__ == "__main__":
    unittest.main()
