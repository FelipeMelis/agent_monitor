"""Tests for the Carbon-based global hotkey manager."""

from __future__ import annotations

import unittest

from agent_monitor.global_hotkeys import (
    CMD_KEY,
    KEYCODE_A,
    OPTION_KEY,
    SHIFT_KEY,
    GlobalHotKeyManager,
    _four_char_code,
)


class FourCharCodeTests(unittest.TestCase):
    def test_known_values_match_carbon_constants(self) -> None:
        self.assertEqual(_four_char_code("keyb"), 0x6B657962)
        self.assertEqual(_four_char_code("----"), 0x2D2D2D2D)
        self.assertEqual(_four_char_code("hkid"), 0x686B6964)


class GlobalHotKeyManagerTests(unittest.TestCase):
    def test_carbon_loads_and_registration_round_trips(self) -> None:
        manager = GlobalHotKeyManager()

        self.assertTrue(manager.available)

        ok = manager.register(
            KEYCODE_A,
            CMD_KEY | OPTION_KEY | SHIFT_KEY,
            lambda: None,
        )

        self.assertTrue(ok)
        manager.unregister_all()

    def test_unregister_all_is_safe_with_nothing_registered(self) -> None:
        manager = GlobalHotKeyManager()

        manager.unregister_all()

    def test_register_without_available_carbon_returns_false(self) -> None:
        manager = GlobalHotKeyManager()
        manager._carbon = None

        ok = manager.register(KEYCODE_A, CMD_KEY, lambda: None)

        self.assertFalse(ok)
        self.assertFalse(manager.available)


if __name__ == "__main__":
    unittest.main()
