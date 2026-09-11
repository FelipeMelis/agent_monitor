"""Tests for display selection and compact panel labels."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import AppKit

from agent_monitor.models import (
    ActivityKind,
    AgentKind,
    AgentSession,
    AttentionKind,
    EvidenceConfidence,
    SessionStatus,
    StateSource,
    StateTransition,
)
from agent_monitor.ui.notch_panel import (
    DETAIL_HEIGHT,
    _attention_panel_frame,
    _attention_title,
    _clamp_swipe_offset,
    _compact_panel_frame,
    _detail_lines,
    _event_phase,
    _evidence_label,
    _expanded_height,
    _filter_dismissed,
    _fluid_frame,
    _focus_failure_message,
    _is_terminal_button_hit,
    _longest_waiting_session,
    _notch_gap_frame,
    _partition_sessions,
    _point_within_frame,
    _presented_name,
    _reduce_motion_enabled,
    _select_target_screen,
    _session_at_y,
    _session_key,
    _should_refresh,
    _stored_bool,
    _stored_sound_name,
    _swipe_dismissed,
    _timeline_lines,
    _truncate,
    _truncate_to_width,
)


def make_rect(
    x: float,
    y: float,
    width: float,
    height: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        origin=SimpleNamespace(x=x, y=y),
        size=SimpleNamespace(width=width, height=height),
    )


class FakeScreen:
    def __init__(
        self,
        frame: SimpleNamespace,
        left: SimpleNamespace,
        right: SimpleNamespace,
        top_inset: float,
    ) -> None:
        self._frame = frame
        self._left = left
        self._right = right
        self._top_inset = top_inset

    def frame(self) -> SimpleNamespace:
        return self._frame

    def auxiliaryTopLeftArea(self) -> SimpleNamespace:
        return self._left

    def auxiliaryTopRightArea(self) -> SimpleNamespace:
        return self._right

    def safeAreaInsets(self) -> SimpleNamespace:
        return SimpleNamespace(top=self._top_inset)


class FakeDefaults:
    def __init__(self, value: bool | str | None) -> None:
        self.value = value

    def objectForKey_(self, key: str) -> bool | str | None:
        return self.value

    def boolForKey_(self, key: str) -> bool:
        return bool(self.value)

    def stringForKey_(self, key: str) -> str | None:
        return self.value if isinstance(self.value, str) else None


class FakeWorkspace:
    def __init__(self, reduce_motion: bool) -> None:
        self.reduce_motion = reduce_motion

    def accessibilityDisplayShouldReduceMotion(self) -> bool:
        return self.reduce_motion


def make_session(name: str, status: SessionStatus) -> AgentSession:
    return AgentSession(
        session_id=name,
        provider=AgentKind.CODEX,
        status=status,
        project=name,
        last_activity=datetime.now(UTC),
        transcript_path=Path(f"{name}.jsonl"),
    )


class NotchPanelTests(unittest.TestCase):
    def test_compact_frame_matches_physical_notch_gap(self) -> None:
        frame = make_rect(0.0, 0.0, 1728.0, 1117.0)
        left = make_rect(0.0, 1085.0, 771.0, 32.0)
        right = make_rect(956.0, 1085.0, 772.0, 32.0)

        notch = _notch_gap_frame(frame, left, right, 32.0)

        self.assertEqual(notch, ((771.0, 1085.0), (185.0, 32.0)))

        screen = FakeScreen(frame, left, right, 32.0)
        compact = _compact_panel_frame(screen)

        self.assertEqual(compact, ((753.0, 1085.0), (221.0, 32.0)))

    def test_attention_banner_drops_below_physical_notch(self) -> None:
        screen = FakeScreen(
            make_rect(0.0, 0.0, 1728.0, 1117.0),
            make_rect(0.0, 1085.0, 771.0, 32.0),
            make_rect(956.0, 1085.0, 772.0, 32.0),
            32.0,
        )

        frame = _attention_panel_frame(screen)

        self.assertEqual(frame, ((684.0, 1013.0), (360.0, 104.0)))

    def test_non_notched_screen_uses_centered_fallback(self) -> None:
        screen = FakeScreen(
            make_rect(0.0, 0.0, 1920.0, 1080.0),
            make_rect(0.0, 0.0, 0.0, 0.0),
            make_rect(0.0, 0.0, 0.0, 0.0),
            0.0,
        )

        compact = _compact_panel_frame(screen)

        self.assertEqual(compact, ((855.0, 1048.0), (210.0, 32.0)))

    def test_fluid_expansion_staggers_height_behind_width(self) -> None:
        start = ((753.0, 1085.0), (221.0, 32.0))
        target = ((649.0, 717.0), (430.0, 400.0))

        early = _fluid_frame(start, target, 0.1, expanding=True)

        self.assertGreater(early[1][0], start[1][0])
        self.assertEqual(early[1][1], start[1][1])
        self.assertAlmostEqual(
            early[0][1] + early[1][1],
            start[0][1] + start[1][1],
        )

    def test_fluid_expansion_overshoots_then_settles(self) -> None:
        start = ((753.0, 1085.0), (221.0, 32.0))
        target = ((649.0, 717.0), (430.0, 400.0))

        elastic = _fluid_frame(start, target, 0.4, expanding=True)
        settled = _fluid_frame(start, target, 1.0, expanding=True)

        self.assertGreater(elastic[1][0], target[1][0])
        self.assertEqual(settled, target)

    def test_fluid_collapse_never_pinches_below_compact_size(self) -> None:
        start = ((649.0, 717.0), (430.0, 400.0))
        target = ((753.0, 1085.0), (221.0, 32.0))

        for step in range(11):
            frame = _fluid_frame(
                start,
                target,
                step / 10,
                expanding=False,
            )
            self.assertGreaterEqual(frame[1][0], target[1][0])
            self.assertGreaterEqual(frame[1][1], target[1][1])

    def test_reduce_motion_preference_is_respected(self) -> None:
        self.assertTrue(_reduce_motion_enabled(FakeWorkspace(True)))
        self.assertFalse(_reduce_motion_enabled(FakeWorkspace(False)))

    def test_point_within_frame_accepts_interior_and_edge_points(
        self,
    ) -> None:
        frame = ((649.0, 717.0), (430.0, 400.0))

        self.assertTrue(_point_within_frame((700.0, 800.0), frame))
        self.assertTrue(_point_within_frame((649.0, 717.0), frame))
        self.assertTrue(_point_within_frame((1079.0, 1117.0), frame))

    def test_point_within_frame_rejects_points_outside_bounds(self) -> None:
        frame = ((649.0, 717.0), (430.0, 400.0))

        self.assertFalse(_point_within_frame((648.0, 800.0), frame))
        self.assertFalse(_point_within_frame((700.0, 1118.0), frame))
        self.assertFalse(_point_within_frame((1080.0, 800.0), frame))

    def test_swipe_dismissed_requires_crossing_threshold(self) -> None:
        self.assertFalse(_swipe_dismissed(40.0, 80.0))
        self.assertFalse(_swipe_dismissed(-79.9, 80.0))
        self.assertTrue(_swipe_dismissed(80.0, 80.0))
        self.assertTrue(_swipe_dismissed(-120.0, 80.0))

    def test_filter_dismissed_hides_a_swiped_row(self) -> None:
        waiting = make_session("agent-monitor", SessionStatus.WAITING)
        key = _session_key(waiting)
        dismissed = {key: SessionStatus.WAITING}

        visible, remaining = _filter_dismissed([waiting], dismissed)

        self.assertEqual(visible, [])
        self.assertEqual(remaining, dismissed)

    def test_filter_dismissed_reveals_row_after_status_changes(self) -> None:
        waiting = make_session("agent-monitor", SessionStatus.WAITING)
        key = _session_key(waiting)
        dismissed = {key: SessionStatus.WAITING}
        retriggered = make_session("agent-monitor", SessionStatus.RUNNING)

        visible, remaining = _filter_dismissed([retriggered], dismissed)

        self.assertEqual(visible, [retriggered])
        self.assertEqual(remaining, {})

    def test_filter_dismissed_drops_stale_entries_for_gone_sessions(
        self,
    ) -> None:
        other = make_session("other-project", SessionStatus.RUNNING)
        stale_key = ("codex", "agent-monitor")
        dismissed = {stale_key: SessionStatus.WAITING}

        visible, remaining = _filter_dismissed([other], dismissed)

        self.assertEqual(visible, [other])
        self.assertEqual(remaining, {})

    def test_clamp_swipe_offset_bounds_both_directions(self) -> None:
        self.assertEqual(_clamp_swipe_offset(40.0, 180.0), 40.0)
        self.assertEqual(_clamp_swipe_offset(300.0, 180.0), 180.0)
        self.assertEqual(_clamp_swipe_offset(-300.0, 180.0), -180.0)

    def test_event_phase_decodes_began_ended_cancelled_changed(self) -> None:
        self.assertEqual(_event_phase(SimpleNamespace(phase=lambda: 0x1)), "began")
        self.assertEqual(_event_phase(SimpleNamespace(phase=lambda: 0x8)), "ended")
        self.assertEqual(
            _event_phase(SimpleNamespace(phase=lambda: 0x10)),
            "cancelled",
        )
        self.assertEqual(
            _event_phase(SimpleNamespace(phase=lambda: 0x4)),
            "changed",
        )

    def test_event_phase_is_none_without_phase_support(self) -> None:
        self.assertIsNone(_event_phase(SimpleNamespace()))

    def test_event_phase_none_for_momentum_only_events(self) -> None:
        self.assertIsNone(_event_phase(SimpleNamespace(phase=lambda: 0)))

    def test_built_in_screen_is_preferred_over_main_screen(self) -> None:
        external = object()
        built_in = object()

        selected = _select_target_screen(
            [external, built_in],
            lambda screen: screen is built_in,
            external,
        )

        self.assertIs(selected, built_in)

    def test_main_screen_is_used_when_laptop_screen_is_unavailable(self) -> None:
        external = object()

        selected = _select_target_screen(
            [external],
            lambda screen: False,
            external,
        )

        self.assertIs(selected, external)

    def test_long_label_uses_ellipsis(self) -> None:
        self.assertEqual(_truncate("abcdefgh", 5), "abcd…")

    def test_truncate_to_width_keeps_short_text_intact(self) -> None:
        text = "Ping"

        self.assertEqual(_truncate_to_width(text, 10_000.0, 10), text)

    def test_truncate_to_width_shortens_long_text_to_fit(self) -> None:
        long_name = (
            "Embeddings p2 (caffeinate) · CLAUDE, a very long real "
            "terminal session name that would overflow a fixed column"
        )

        result = _truncate_to_width(long_name, 150.0, 10)

        self.assertLess(len(result), len(long_name))
        self.assertTrue(result.endswith("…"))

    def test_truncate_to_width_never_exceeds_the_measured_budget(
        self,
    ) -> None:
        long_name = "A very long real terminal session name" * 3

        for budget in (20.0, 60.0, 120.0, 300.0):
            result = _truncate_to_width(long_name, budget, 10)
            measured = (
                AppKit.NSString.stringWithString_(result)
                .sizeWithAttributes_(
                    {
                        AppKit.NSFontAttributeName: (
                            AppKit.NSFont.systemFontOfSize_weight_(
                                10,
                                AppKit.NSFontWeightRegular,
                            )
                        )
                    }
                )
                .width
            )
            self.assertLessEqual(measured, budget)

    def test_sessions_are_partitioned_into_active_and_recent(self) -> None:
        running = make_session("running", SessionStatus.RUNNING)
        waiting = make_session("waiting", SessionStatus.WAITING)
        complete = make_session("complete", SessionStatus.COMPLETE)
        idle = make_session("idle", SessionStatus.IDLE)

        active, recent = _partition_sessions(
            [waiting, running, complete, idle]
        )

        self.assertEqual(active, [waiting, running])
        self.assertEqual(recent, [complete, idle])

    def test_longest_waiting_session_ignores_non_waiting_sessions(
        self,
    ) -> None:
        running = make_session("running", SessionStatus.RUNNING)
        complete = make_session("complete", SessionStatus.COMPLETE)

        self.assertIsNone(
            _longest_waiting_session([running, complete]),
        )

    def test_longest_waiting_session_picks_the_oldest_wait(self) -> None:
        older = replace(
            make_session("older", SessionStatus.WAITING),
            last_activity=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer = replace(
            make_session("newer", SessionStatus.WAITING),
            last_activity=datetime(2026, 1, 2, tzinfo=UTC),
        )

        self.assertIs(
            _longest_waiting_session([newer, older]),
            older,
        )

    def test_expanded_height_includes_each_section_heading(self) -> None:
        running = make_session("running", SessionStatus.RUNNING)
        complete = make_session("complete", SessionStatus.COMPLETE)

        active_only = _expanded_height([running])
        mixed = _expanded_height([running, complete])

        self.assertEqual(active_only, 130.0)
        self.assertEqual(mixed, 208.0)

    def test_selected_session_adds_detail_card_height(self) -> None:
        running = make_session("running", SessionStatus.RUNNING)

        unselected = _expanded_height([running])
        selected = _expanded_height([running], _session_key(running))

        self.assertEqual(selected, unselected + DETAIL_HEIGHT)

    def test_row_hit_testing_accounts_for_section_headers(self) -> None:
        running = make_session("running", SessionStatus.RUNNING)
        complete = make_session("complete", SessionStatus.COMPLETE)

        active_hit = _session_at_y([running, complete], 75.0)
        recent_hit = _session_at_y([running, complete], 155.0)
        heading_hit = _session_at_y([running, complete], 135.0)

        self.assertIs(active_hit, running)
        self.assertIs(recent_hit, complete)
        self.assertIsNone(heading_hit)

    def test_detail_lines_exclude_action_and_transcript_path(self) -> None:
        session = AgentSession(
            session_id="private",
            provider=AgentKind.CLAUDE,
            status=SessionStatus.WAITING,
            project="full-project-name",
            last_activity=datetime.now(UTC),
            transcript_path=Path("secret-transcript.jsonl"),
            action="secret command",
            model="claude-model",
            branch="feature-branch",
            token_count=1234,
            status_reason="approval is unresolved",
            attention_kind=AttentionKind.EDIT,
            attention_summary="src/app.py",
        )

        value = "\n".join(_detail_lines(session))

        self.assertIn("full-project-name · CLAUDE", value)
        self.assertIn("approval is unresolved", value)
        self.assertIn("Edit approval required: src/app.py", value)
        self.assertIn("claude-model", value)
        self.assertIn("feature-branch", value)
        self.assertIn("1,234", value)
        self.assertNotIn("secret command", value)
        self.assertNotIn("secret-transcript.jsonl", value)

    def test_presented_name_falls_back_to_folder_name(self) -> None:
        session = make_session("agent-monitor", SessionStatus.WAITING)

        self.assertEqual(_presented_name(session, {}), "agent-monitor")
        self.assertEqual(_presented_name(session, None), "agent-monitor")

    def test_presented_name_prefers_mapped_iterm_name(self) -> None:
        session = replace(
            make_session("agent-monitor", SessionStatus.WAITING),
            terminal_session_id="w0t1p0:ABC/123",
        )

        name = _presented_name(
            session,
            {"w0t1p0:ABC/123": "my renamed tab"},
        )

        self.assertEqual(name, "my renamed tab")

    def test_presented_name_ignores_unrelated_terminal_names(self) -> None:
        session = replace(
            make_session("agent-monitor", SessionStatus.WAITING),
            terminal_session_id="w0t1p0:ABC/123",
        )

        name = _presented_name(session, {"w0t1p0:XYZ/999": "other tab"})

        self.assertEqual(name, "agent-monitor")

    def test_detail_lines_uses_presented_name_when_available(self) -> None:
        session = replace(
            make_session("agent-monitor", SessionStatus.WAITING),
            terminal_session_id="w0t1p0:ABC/123",
        )

        value = "\n".join(
            _detail_lines(
                session,
                {"w0t1p0:ABC/123": "my renamed tab"},
            )
        )

        self.assertIn("my renamed tab · CODEX", value)

    def test_attention_title_distinguishes_input_from_approval(self) -> None:
        waiting = make_session("waiting", SessionStatus.WAITING)
        edit = replace(waiting, attention_kind=AttentionKind.EDIT)
        user_input = replace(waiting, attention_kind=AttentionKind.INPUT)

        self.assertEqual(_attention_title(edit), "Edit approval required")
        self.assertEqual(_attention_title(user_input), "Input required")

    def test_timeline_explains_state_change_and_source(self) -> None:
        session = replace(
            make_session("project", SessionStatus.WAITING),
            transitions=(
                StateTransition(
                    from_status=SessionStatus.RUNNING,
                    to_status=SessionStatus.WAITING,
                    from_activity=ActivityKind.RUNNING_COMMAND,
                    to_activity=ActivityKind.WAITING_APPROVAL,
                    occurred_at=datetime.now(UTC),
                    reason="an explicit provider hook is unresolved",
                    source=StateSource.HOOK,
                    confidence=EvidenceConfidence.CONFIRMED,
                ),
            ),
        )

        value = "\n".join(_timeline_lines(session))

        self.assertIn("running → waiting", value)
        self.assertIn("hook", value)
        self.assertIn("explicit provider hook", value)
        self.assertIn("running command → waiting approval", value)
        self.assertIn("Confirmed", value)

    def test_evidence_label_combines_confidence_and_source(self) -> None:
        session = replace(
            make_session("project", SessionStatus.RUNNING),
            confidence=EvidenceConfidence.INFERRED,
            evidence_source=StateSource.TRANSCRIPT,
        )

        self.assertEqual(_evidence_label(session), "Inferred (transcript)")

    def test_periodic_refresh_is_blocked_only_while_paused(self) -> None:
        self.assertFalse(_should_refresh(paused=True, timer_fired=True))
        self.assertTrue(_should_refresh(paused=True, timer_fired=False))
        self.assertTrue(_should_refresh(paused=False, timer_fired=True))

    def test_sound_preference_defaults_on_and_reads_saved_value(self) -> None:
        self.assertTrue(_stored_bool(FakeDefaults(None), "sound", default=True))
        self.assertFalse(
            _stored_bool(FakeDefaults(False), "sound", default=True)
        )

    def test_sound_name_defaults_and_reads_saved_valid_value(self) -> None:
        self.assertEqual(
            _stored_sound_name(FakeDefaults(None), "sound", default="Glass"),
            "Glass",
        )
        self.assertEqual(
            _stored_sound_name(FakeDefaults("Ping"), "sound", default="Glass"),
            "Ping",
        )

    def test_sound_name_falls_back_for_an_unknown_saved_value(self) -> None:
        self.assertEqual(
            _stored_sound_name(
                FakeDefaults("NotARealSound"),
                "sound",
                default="Glass",
            ),
            "Glass",
        )

    def test_terminal_focus_button_has_a_bounded_hit_target(self) -> None:
        self.assertFalse(_is_terminal_button_hit(377.9))
        self.assertTrue(_is_terminal_button_hit(378.0))
        self.assertTrue(_is_terminal_button_hit(415.9))
        self.assertFalse(_is_terminal_button_hit(416.0))

    def test_focus_failure_explains_unlinked_and_closed_sessions(self) -> None:
        session = make_session("project", SessionStatus.RUNNING)

        self.assertEqual(
            _focus_failure_message(session),
            "Terminal not linked — restart or resume this agent",
        )
        self.assertEqual(
            _focus_failure_message(
                replace(session, terminal_session_id="w0t1p0:ABC")
            ),
            "Terminal tab unavailable — reopen or relink this session",
        )

    def test_details_do_not_promise_existing_session_prompt_mapping(
        self,
    ) -> None:
        session = make_session("project", SessionStatus.RUNNING)

        value = "\n".join(_detail_lines(session))

        self.assertIn("restart or resume this agent", value)
        self.assertNotIn("next prompt", value)


if __name__ == "__main__":
    unittest.main()
