"""Small AppKit panel anchored to the top center of the active display."""

from __future__ import annotations

import ctypes
import math
import time
from collections.abc import Callable
from typing import Any

from agent_monitor.debug import format_debug_snapshot
from agent_monitor.global_hotkeys import (
    CMD_KEY,
    KEYCODE_A,
    KEYCODE_J,
    KEYCODE_P,
    OPTION_KEY,
    GlobalHotKeyManager,
)
from agent_monitor.models import AgentSession, SessionStatus
from agent_monitor.notifications import WaitingNotificationManager
from agent_monitor.terminal_focus import TerminalFocuser
from agent_monitor.terminal_names import TerminalNameLookup

try:
    import AppKit
    import objc
    from Foundation import NSObject, NSString, NSTimer, NSUserDefaults
except ImportError:  # pragma: no cover - exercised on non-macOS CI
    AppKit = None
    objc = None
    NSObject = object
    NSString = None
    NSTimer = None
    NSUserDefaults = None

FALLBACK_CLOSED_WIDTH = 210.0
CLOSED_HEIGHT = 32.0
COMPACT_WING_WIDTH = 18.0
ATTENTION_WIDTH = 360.0
ATTENTION_HEIGHT = 104.0
ATTENTION_DURATION = 7.0
OPEN_WIDTH = 430.0
HEADER_HEIGHT = 44.0
ROW_HEIGHT = 52.0
SECTION_HEADER_HEIGHT = 26.0
DETAIL_HEIGHT = 294.0
MAX_VISIBLE_SESSIONS = 7
HOVER_EXIT_DELAY = 0.25
SWIPE_DISMISS_THRESHOLD = 44.0
SWIPE_MAX_OFFSET = 180.0
SWIPE_REVEAL_DEADZONE = 12.0
TERMINAL_NAME_REFRESH_INTERVAL = 7.5
ACTIVE_STATUSES = {SessionStatus.WAITING, SessionStatus.RUNNING}
SessionKey = tuple[str, str]
APPROVAL_SOUNDS_DEFAULTS_KEY = "AgentMonitorApprovalSoundsEnabled"
APPROVAL_SOUND_NAME_DEFAULTS_KEY = "AgentMonitorApprovalSoundName"
APPROVAL_SOUND_NAMES = (
    "Basso",
    "Blow",
    "Bottle",
    "Frog",
    "Funk",
    "Glass",
    "Hero",
    "Morse",
    "Ping",
    "Pop",
    "Purr",
    "Sosumi",
    "Submarine",
    "Tink",
)
DEFAULT_APPROVAL_SOUND_NAME = "Glass"
TERMINAL_BUTTON_LEFT = 378.0
TERMINAL_BUTTON_RIGHT = 416.0
EXPAND_DURATION = 0.32
COLLAPSE_DURATION = 0.26
ANIMATION_INTERVAL = 1.0 / 60.0
PanelFrame = tuple[tuple[float, float], tuple[float, float]]


def _truncate(text: str, limit: int) -> str:
    """Shorten a label without leaving a visually abrupt hard cut."""

    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _truncate_to_width(
    text: str,
    max_width: float,
    size: float,
    *,
    bold: bool = False,
) -> str:
    """Shorten text to fit an actual rendered pixel width.

    Character-count truncation is unreliable once real (often long)
    terminal session names are shown instead of short folder names, so
    layouts that must not overlap neighboring text measure the real
    width instead of guessing a character limit.
    """

    if AppKit is None or NSString is None:
        return text
    weight = (
        AppKit.NSFontWeightSemibold if bold else AppKit.NSFontWeightRegular
    )
    font = AppKit.NSFont.systemFontOfSize_weight_(size, weight)
    attributes = {AppKit.NSFontAttributeName: font}

    def width_of(value: str) -> float:
        return (
            NSString.stringWithString_(value)
            .sizeWithAttributes_(attributes)
            .width
        )

    if width_of(text) <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if width_of(text[:mid] + "…") <= max_width:
            low = mid
        else:
            high = mid - 1
    return f"{text[:low]}…" if low > 0 else "…"


def _select_target_screen(
    screens: list[Any],
    is_built_in: Callable[[Any], bool],
    fallback: Any,
) -> Any:
    """Prefer the laptop display and fall back when it is unavailable."""

    return next((screen for screen in screens if is_built_in(screen)), fallback)


def _notch_gap_frame(
    screen_frame: Any,
    left_area: Any,
    right_area: Any,
    top_inset: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return the physical notch gap from AppKit auxiliary screen areas."""

    left_edge = left_area.origin.x + left_area.size.width
    right_edge = right_area.origin.x
    width = right_edge - left_edge
    height = min(
        top_inset,
        left_area.size.height,
        right_area.size.height,
    )
    if width <= 0 or height <= 0:
        return None
    y = screen_frame.origin.y + screen_frame.size.height - height
    return ((left_edge, y), (width, height))


def _compact_panel_frame(
    screen: Any,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Add small visible wings around a notch or use a fallback pill."""

    frame = screen.frame()
    notch_frame = _notch_gap_frame(
        frame,
        screen.auxiliaryTopLeftArea(),
        screen.auxiliaryTopRightArea(),
        screen.safeAreaInsets().top,
    )
    if notch_frame is not None:
        (x, y), (width, height) = notch_frame
        return (
            (x - COMPACT_WING_WIDTH, y),
            (width + (2 * COMPACT_WING_WIDTH), height),
        )
    x = frame.origin.x + (
        (frame.size.width - FALLBACK_CLOSED_WIDTH) / 2
    )
    y = frame.origin.y + frame.size.height - CLOSED_HEIGHT
    return ((x, y), (FALLBACK_CLOSED_WIDTH, CLOSED_HEIGHT))


def _attention_panel_frame(
    screen: Any,
) -> PanelFrame:
    """Return a top-centered frame that drops below the physical notch."""

    frame = screen.frame()
    x = frame.origin.x + ((frame.size.width - ATTENTION_WIDTH) / 2)
    y = frame.origin.y + frame.size.height - ATTENTION_HEIGHT
    return ((x, y), (ATTENTION_WIDTH, ATTENTION_HEIGHT))


def _expanded_panel_frame(screen: Any, height: float) -> PanelFrame:
    """Return a top-centered expanded panel frame."""

    frame = screen.frame()
    x = frame.origin.x + ((frame.size.width - OPEN_WIDTH) / 2)
    y = frame.origin.y + frame.size.height - height
    return ((x, y), (OPEN_WIDTH, height))


def _spring_progress(progress: float, *, delay: float = 0.0) -> float:
    """Return a restrained spring that settles exactly at one."""

    if progress <= delay:
        return 0.0
    if progress >= 1.0:
        return 1.0
    shifted = (progress - delay) / (1.0 - delay)
    value = 1.0 - (math.exp(-6.0 * shifted) * math.cos(8.0 * shifted))
    return min(value, 1.055)


def _ease_out_progress(progress: float, *, delay: float = 0.0) -> float:
    """Return a smooth non-overshooting progress value."""

    if progress <= delay:
        return 0.0
    if progress >= 1.0:
        return 1.0
    shifted = (progress - delay) / (1.0 - delay)
    return 1.0 - ((1.0 - shifted) ** 3)


def _fluid_frame(
    start: PanelFrame,
    target: PanelFrame,
    progress: float,
    *,
    expanding: bool,
) -> PanelFrame:
    """Interpolate a top-anchored frame with staggered liquid motion."""

    bounded = min(max(progress, 0.0), 1.0)
    if expanding:
        width_progress = _spring_progress(bounded)
        height_progress = _spring_progress(bounded, delay=0.12)
    else:
        height_progress = _ease_out_progress(bounded)
        width_progress = _ease_out_progress(bounded, delay=0.12)

    (start_x, start_y), (start_width, start_height) = start
    (target_x, target_y), (target_width, target_height) = target
    return (
        (
            start_x + ((target_x - start_x) * width_progress),
            start_y + ((target_y - start_y) * height_progress),
        ),
        (
            start_width
            + ((target_width - start_width) * width_progress),
            start_height
            + ((target_height - start_height) * height_progress),
        ),
    )


def _frame_tuple(frame: Any) -> PanelFrame:
    """Convert an AppKit frame into a testable tuple."""

    return (
        (float(frame.origin.x), float(frame.origin.y)),
        (float(frame.size.width), float(frame.size.height)),
    )


def _reduce_motion_enabled(workspace: Any) -> bool:
    """Read the system accessibility preference when it is available."""

    try:
        return bool(workspace.accessibilityDisplayShouldReduceMotion())
    except AttributeError:
        return False


def _point_within_frame(
    point: tuple[float, float],
    frame: PanelFrame,
) -> bool:
    """Return whether a screen point falls inside a panel frame."""

    (x, y), (width, height) = frame
    point_x, point_y = point
    return x <= point_x <= x + width and y <= point_y <= y + height


def _partition_sessions(
    sessions: list[AgentSession],
) -> tuple[list[AgentSession], list[AgentSession]]:
    """Split sessions into active work and recent history."""

    active = [
        session for session in sessions if session.status in ACTIVE_STATUSES
    ]
    recent = [
        session for session in sessions if session.status not in ACTIVE_STATUSES
    ]
    return active, recent


def _longest_waiting_session(
    sessions: list[AgentSession],
) -> AgentSession | None:
    """Return the WAITING session that has been waiting the longest."""

    waiting = [
        session
        for session in sessions
        if session.status is SessionStatus.WAITING
    ]
    if not waiting:
        return None
    return min(waiting, key=lambda session: session.last_activity)


def _session_key(session: AgentSession) -> SessionKey:
    return session.provider.value, session.session_id


def _filter_dismissed(
    sessions: list[AgentSession],
    dismissed: dict[SessionKey, SessionStatus],
) -> tuple[list[AgentSession], dict[SessionKey, SessionStatus]]:
    """Hide swiped-away rows until their status changes (a retrigger)."""

    visible: list[AgentSession] = []
    remaining: dict[SessionKey, SessionStatus] = {}
    for session in sessions:
        key = _session_key(session)
        if key in dismissed and dismissed[key] == session.status:
            remaining[key] = dismissed[key]
            continue
        visible.append(session)
    return visible, remaining


def _swipe_dismissed(accumulated_delta: float, threshold: float) -> bool:
    """Return whether a horizontal trackpad swipe has crossed the
    dismiss threshold, in either direction."""

    return abs(accumulated_delta) >= threshold


def _clamp_swipe_offset(accumulated_delta: float, max_offset: float) -> float:
    return max(-max_offset, min(max_offset, accumulated_delta))


_PHASE_BEGAN = 0x1
_PHASE_ENDED = 0x8
_PHASE_CANCELLED = 0x10


def _event_phase(event: Any) -> str | None:
    """Decode an NSEvent's scroll phase, or None if unsupported.

    Devices that report phases (trackpads) let a swipe reveal the red
    dismiss bar as it moves and only commit on release. Devices that
    don't (plain scroll wheels) fall back to resolving immediately.
    """

    try:
        phase = int(event.phase())
    except AttributeError:
        return None
    if phase == 0:
        # NSEventPhaseNone: a momentum-only (inertial) event after the
        # fingers already lifted, or a device with no phase reporting
        # despite exposing phase(). Neither reflects live user input.
        return None
    if phase & _PHASE_BEGAN:
        return "began"
    if phase & _PHASE_ENDED:
        return "ended"
    if phase & _PHASE_CANCELLED:
        return "cancelled"
    return "changed"


def _visible_sessions(
    sessions: list[AgentSession],
) -> list[AgentSession]:
    active, recent = _partition_sessions(sessions[:MAX_VISIBLE_SESSIONS])
    return [*active, *recent]


def _selected_session(
    sessions: list[AgentSession],
    selected_key: SessionKey | None,
) -> AgentSession | None:
    if selected_key is None:
        return None
    return next(
        (
            session
            for session in _visible_sessions(sessions)
            if _session_key(session) == selected_key
        ),
        None,
    )


def _row_layout(
    sessions: list[AgentSession],
) -> list[tuple[AgentSession, float]]:
    """Return each visible row and its top edge in the expanded panel."""

    active, recent = _partition_sessions(sessions[:MAX_VISIBLE_SESSIONS])
    rows: list[tuple[AgentSession, float]] = []
    top = HEADER_HEIGHT
    for section in (active, recent):
        if not section:
            continue
        top += SECTION_HEADER_HEIGHT
        for session in section:
            rows.append((session, top))
            top += ROW_HEIGHT
    return rows


def _session_at_y(
    sessions: list[AgentSession],
    y: float,
) -> AgentSession | None:
    return next(
        (
            session
            for session, top in _row_layout(sessions)
            if top <= y < top + ROW_HEIGHT
        ),
        None,
    )


def _attention_title(session: AgentSession) -> str:
    """Describe the kind of response an agent is waiting for."""

    if session.attention_kind is None:
        return "Attention required"
    if session.attention_kind.value == "input":
        return "Input required"
    return f"{session.attention_kind.value.title()} approval required"


def _presented_name(
    session: AgentSession,
    terminal_names: dict[str, str] | None = None,
) -> str:
    """Prefer the live iTerm2 session name over the folder-derived one."""

    if session.terminal_session_id and terminal_names:
        override = terminal_names.get(session.terminal_session_id)
        if override:
            return override
    return session.display_name


def _detail_lines(
    session: AgentSession,
    terminal_names: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Return privacy-safe session metadata for the detail card."""

    provider = session.provider.value.upper()
    reason = session.status_reason or "No classification reason recorded"
    model = session.model or "unknown"
    branch = session.branch or "unknown"
    updated = session.last_activity.astimezone().strftime("%H:%M:%S")
    lines = [
        f"{_presented_name(session, terminal_names)} · {provider}",
        f"{session.status.value}: {reason}",
        (
            f"Activity: {session.activity.value.title()} · Evidence: "
            f"{_evidence_label(session)}"
        ),
    ]
    if session.attention_kind is not None:
        summary = session.attention_summary or "No request summary available"
        lines.append(f"{_attention_title(session)}: {summary}")
    lines.extend(
        (
            f"Model: {model} · Branch: {branch}",
            f"Tokens: {session.token_count:,} · Updated: {updated}",
            (
                "Terminal: Double-click to focus iTerm2"
                if session.terminal_session_id
                else "Terminal: not linked — restart or resume this agent"
            ),
        )
    )
    return tuple(lines)


def _focus_failure_message(session: AgentSession) -> str:
    """Explain why a terminal focus request could not be completed."""

    if not session.terminal_session_id:
        return "Terminal not linked — restart or resume this agent"
    return "Terminal tab unavailable — reopen or relink this session"


def _timeline_lines(session: AgentSession) -> tuple[str, ...]:
    """Format recent privacy-safe state changes, newest first."""

    lines = []
    for transition in session.transitions[:3]:
        occurred = transition.occurred_at.astimezone().strftime("%H:%M:%S")
        previous_status = (
            transition.from_status.value if transition.from_status else "new"
        )
        activity_change = transition.to_activity.value
        if (
            transition.from_activity is not None
            and transition.from_activity is not transition.to_activity
        ):
            activity_change = (
                f"{transition.from_activity.value} → "
                f"{transition.to_activity.value}"
            )
        lines.extend(
            (
                (
                    f"{occurred} · {previous_status} → "
                    f"{transition.to_status.value} · {activity_change}"
                ),
                (
                    f"{transition.confidence.value.title()} "
                    f"{transition.source.value} · {transition.reason}"
                ),
            )
        )
    return tuple(lines)


def _evidence_label(session: AgentSession) -> str:
    return (
        f"{session.confidence.value.title()} "
        f"({session.evidence_source.value})"
    )


def _expanded_height(
    sessions: list[AgentSession],
    selected_key: SessionKey | None = None,
) -> float:
    """Return the expanded height including visible section headings."""

    if not sessions:
        return HEADER_HEIGHT + ROW_HEIGHT + 8
    active, recent = _partition_sessions(sessions[:MAX_VISIBLE_SESSIONS])
    section_count = int(bool(active)) + int(bool(recent))
    detail_height = (
        DETAIL_HEIGHT
        if _selected_session(sessions, selected_key) is not None
        else 0.0
    )
    return (
        HEADER_HEIGHT
        + (section_count * SECTION_HEADER_HEIGHT)
        + ((len(active) + len(recent)) * ROW_HEIGHT)
        + detail_height
        + 8
    )


def _should_refresh(*, paused: bool, timer_fired: bool) -> bool:
    """Allow manual refreshes while periodic monitoring is paused."""

    return not paused or not timer_fired


def _is_terminal_button_hit(x: float) -> bool:
    """Return whether a row click targets its terminal-focus control."""

    return TERMINAL_BUTTON_LEFT <= x < TERMINAL_BUTTON_RIGHT


def _stored_bool(defaults: Any, key: str, *, default: bool) -> bool:
    """Read a bool while preserving a default for an unset preference."""

    if defaults.objectForKey_(key) is None:
        return default
    return bool(defaults.boolForKey_(key))


def _stored_sound_name(defaults: Any, key: str, *, default: str) -> str:
    """Read the chosen sound name, falling back to a known-good default."""

    stored = defaults.stringForKey_(key)
    if stored and stored in APPROVAL_SOUND_NAMES:
        return stored
    return default


def _age_label(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3_600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3_600)}h"


if AppKit is not None:

    class SessionPanelView(AppKit.NSView):
        """Draw the compact status pill and expanded session list."""

        def initWithFrame_(self, frame: Any) -> SessionPanelView:
            self = objc.super(SessionPanelView, self).initWithFrame_(  # noqa: PLW0642
                frame
            )
            if self is None:
                return self
            self.sessions: list[AgentSession] = []
            self.expanded = False
            self.paused = False
            self.selected_key: SessionKey | None = None
            self.on_expansion: Callable[[bool], None] | None = None
            self.on_focus: Callable[[AgentSession], None] | None = None
            self.tracking_area = None
            self.exit_timer = None
            self.attention_session: AgentSession | None = None
            self.attention_timer = None
            self.focus_failure_key: SessionKey | None = None
            self.focus_failure_message = ""
            self.shell_progress = 0.0
            self.shell_kind = "expanded"
            self.dismissed_sessions: dict[SessionKey, SessionStatus] = {}
            self.swipe_key: SessionKey | None = None
            self.swipe_accumulated = 0.0
            self.terminal_names: dict[str, str] = {}
            return self

        def isFlipped(self) -> bool:
            return True

        def acceptsFirstMouse_(self, event: Any) -> bool:
            return True

        def updateTrackingAreas(self) -> None:
            if self.tracking_area is not None:
                self.removeTrackingArea_(self.tracking_area)
            options = (
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveAlways
                | AppKit.NSTrackingInVisibleRect
            )
            self.tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                options,
                self,
                None,
            )
            self.addTrackingArea_(self.tracking_area)
            objc.super(SessionPanelView, self).updateTrackingAreas()

        def mouseEntered_(self, event: Any) -> None:
            if self.exit_timer is not None:
                self.exit_timer.invalidate()
                self.exit_timer = None
            if self.attention_session is not None:
                # Don't steal the click: while the banner is up, only an
                # explicit click should act (jump to terminal), not a
                # passing hover on the way to it.
                return
            self._set_expanded(True)

        def mouseExited_(self, event: Any) -> None:
            if self.exit_timer is not None:
                self.exit_timer.invalidate()
            self.exit_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                HOVER_EXIT_DELAY,
                self,
                "collapseAfterExit:",
                None,
                False,
            )

        def collapseAfterExit_(self, timer: Any) -> None:
            self.exit_timer = None
            window = self.window()
            if window is not None:
                location = AppKit.NSEvent.mouseLocation()
                frame = _frame_tuple(window.frame())
                if _point_within_frame(
                    (location.x, location.y),
                    frame,
                ):
                    # A spurious exit (tracking-area churn mid-resize)
                    # while the cursor is still over the panel. Recheck
                    # shortly instead of dropping the collapse entirely,
                    # so a later genuine exit is never stranded.
                    self.exit_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        HOVER_EXIT_DELAY,
                        self,
                        "collapseAfterExit:",
                        None,
                        False,
                    )
                    return
            self._set_expanded(False)

        def mouseDown_(self, event: Any) -> None:
            if not self.expanded:
                if (
                    self.attention_session is not None
                    and self.on_focus is not None
                ):
                    self.on_focus(self.attention_session)
                    return
                self._set_expanded(True)
                return
            point = self.convertPoint_fromView_(
                event.locationInWindow(),
                None,
            )
            session = _session_at_y(self.sessions, point.y)
            if session is None:
                return
            if _is_terminal_button_hit(point.x):
                if self.on_focus is not None:
                    self.on_focus(session)
                return
            key = _session_key(session)
            self.selected_key = None if key == self.selected_key else key
            if self.on_expansion is not None:
                self.on_expansion(True)
            self.setNeedsDisplay_(True)

        def scrollWheel_(self, event: Any) -> None:
            """Reveal a red dismiss bar on a horizontal trackpad swipe.

            The row slides and the bar fills in as you swipe; it only
            commits to dismissing the row once you release past the
            threshold, so a short or aborted swipe just snaps back.
            """

            if not self.expanded:
                return
            delta_x = float(event.deltaX())
            delta_y = float(event.deltaY())
            phase = _event_phase(event)

            if phase == "began":
                self._begin_swipe(event)
                return

            if self.swipe_key is None:
                if abs(delta_x) <= abs(delta_y):
                    return
                self._begin_swipe(event)
                if self.swipe_key is None:
                    return

            self.swipe_accumulated += delta_x
            self.swipe_offset = _clamp_swipe_offset(
                self.swipe_accumulated,
                SWIPE_MAX_OFFSET,
            )
            self.setNeedsDisplay_(True)

            if phase in ("ended", "cancelled"):
                self._resolve_swipe()
            elif phase is None and _swipe_dismissed(
                self.swipe_accumulated,
                SWIPE_DISMISS_THRESHOLD,
            ):
                # Devices with no phase reporting (plain scroll wheels)
                # have no release signal, so commit as soon as the
                # threshold is crossed rather than waiting forever.
                self._resolve_swipe()

        def _begin_swipe(self, event: Any) -> None:
            point = self.convertPoint_fromView_(
                event.locationInWindow(),
                None,
            )
            session = _session_at_y(self.sessions, point.y)
            if session is None:
                self.swipe_key = None
                self.swipe_accumulated = 0.0
                self.swipe_offset = 0.0
                return
            self.swipe_key = _session_key(session)
            self.swipe_accumulated = 0.0
            self.swipe_offset = 0.0

        def _resolve_swipe(self) -> None:
            key = self.swipe_key
            if key is None:
                return
            if not _swipe_dismissed(
                self.swipe_accumulated,
                SWIPE_DISMISS_THRESHOLD,
            ):
                self.swipe_key = None
                self.swipe_accumulated = 0.0
                self.swipe_offset = 0.0
                self.setNeedsDisplay_(True)
                return
            session = next(
                (
                    candidate
                    for candidate in self.sessions
                    if _session_key(candidate) == key
                ),
                None,
            )
            self.swipe_key = None
            self.swipe_accumulated = 0.0
            self.swipe_offset = 0.0
            if session is None:
                return
            self.dismissed_sessions[key] = session.status
            self.sessions = [
                remaining
                for remaining in self.sessions
                if _session_key(remaining) != key
            ]
            if self.selected_key == key:
                self.selected_key = None
            if self.on_expansion is not None:
                self.on_expansion(True)
            self.setNeedsDisplay_(True)

        def _set_expanded(self, expanded: bool) -> None:
            self.swipe_key = None
            self.swipe_accumulated = 0.0
            if self.expanded == expanded:
                return
            self.expanded = expanded
            if self.on_expansion is not None:
                self.on_expansion(expanded)
            self.setNeedsDisplay_(True)

        def set_sessions(self, sessions: list[AgentSession]) -> None:
            visible, self.dismissed_sessions = _filter_dismissed(
                sessions,
                self.dismissed_sessions,
            )
            self.sessions = visible
            failed_session = _selected_session(
                sessions,
                self.focus_failure_key,
            )
            if (
                failed_session is None
                or (
                    failed_session.terminal_session_id
                    and self.focus_failure_message.startswith(
                        "Terminal not linked"
                    )
                )
            ):
                self.clear_focus_failure()
            if self.attention_session is not None:
                attention_key = _session_key(self.attention_session)
                still_waiting = any(
                    _session_key(session) == attention_key
                    and session.status is SessionStatus.WAITING
                    for session in sessions
                )
                if not still_waiting:
                    self._clear_attention()
            if _selected_session(sessions, self.selected_key) is None:
                selection_changed = self.selected_key is not None
                self.selected_key = None
                if (
                    selection_changed
                    and self.expanded
                    and self.on_expansion is not None
                ):
                    self.on_expansion(True)
            self.setNeedsDisplay_(True)

        def show_focus_failure(self, session: AgentSession) -> None:
            """Keep terminal navigation failures visible in the clicked row."""

            self.focus_failure_key = _session_key(session)
            self.focus_failure_message = _focus_failure_message(session)
            self.selected_key = self.focus_failure_key
            if self.on_expansion is not None:
                self.on_expansion(True)
            self.setNeedsDisplay_(True)

        def clear_focus_failure(self) -> None:
            self.focus_failure_key = None
            self.focus_failure_message = ""
            self.setNeedsDisplay_(True)

        def set_paused(self, paused: bool) -> None:
            self.paused = paused
            self.setNeedsDisplay_(True)

        def set_terminal_names(self, names: dict[str, str]) -> None:
            if names == self.terminal_names:
                return
            self.terminal_names = names
            self.setNeedsDisplay_(True)

        def show_attention(self, session: AgentSession) -> None:
            """Temporarily reveal a new waiting request below the notch."""

            if self.attention_timer is not None:
                self.attention_timer.invalidate()
            self.attention_session = session
            self.attention_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                ATTENTION_DURATION,
                self,
                "hideAttention:",
                None,
                False,
            )
            if not self.expanded and self.on_expansion is not None:
                self.on_expansion(False)
            self.setNeedsDisplay_(True)

        def hideAttention_(self, timer: Any) -> None:
            self.attention_timer = None
            self._clear_attention()

        def _clear_attention(self) -> None:
            if self.attention_timer is not None:
                self.attention_timer.invalidate()
                self.attention_timer = None
            self.attention_session = None
            if not self.expanded and self.on_expansion is not None:
                self.on_expansion(False)
            self.setNeedsDisplay_(True)

        def drawRect_(self, dirty_rect: Any) -> None:
            self._draw_background()
            if self.expanded:
                self._draw_expanded()
            elif self.shell_progress > 0.0:
                if self.shell_kind == "attention":
                    self._draw_attention()
                else:
                    self._draw_expanded()
            elif self.attention_session is not None:
                self._draw_attention()
            else:
                self._draw_compact()

        def _draw_background(self) -> None:
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.025, 0.97).set()
            if self.attention_session is not None and not self.expanded:
                radius = 15.0
            else:
                radius = 11.0 + (4.0 * self.shell_progress)
            path = (
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    self.bounds(),
                    radius,
                    radius,
                )
            )
            path.fill()

        def _draw_compact(self) -> None:
            width = self.bounds().size.width
            if self.paused:
                AppKit.NSColor.systemOrangeColor().set()
                _fill_oval(((6, 12), (8, 8)))
                _draw_text("Ⅱ", width - 17, 8, 11,
                           AppKit.NSColor.whiteColor(), bold=True)
                return
            active, recent = _partition_sessions(self.sessions)
            urgent = active[0] if active else (recent[0] if recent else None)
            color = _status_color(urgent.status if urgent else None)
            color.set()
            _fill_oval(((6, 12), (8, 8)))

            label = str(len(active)) if active else "–"
            label_x = width - 7 - (len(label) * 7)
            _draw_text(
                label,
                label_x,
                8,
                11,
                AppKit.NSColor.whiteColor(),
                bold=True,
            )

        def _draw_attention(self) -> None:
            self._draw_compact()
            session = self.attention_session
            if session is None:
                return
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.1).set()
            _fill_rect(((14, CLOSED_HEIGHT), (ATTENTION_WIDTH - 28, 1)))
            _draw_text(
                _attention_title(session),
                18,
                42,
                13,
                AppKit.NSColor.systemRedColor(),
                bold=True,
            )
            provider = session.provider.value.upper()
            line_width = ATTENTION_WIDTH - 36
            name_line = _truncate_to_width(
                (
                    f"{_presented_name(session, self.terminal_names)} · "
                    f"{provider}"
                ),
                line_width,
                10,
            )
            _draw_text(
                name_line,
                18,
                61,
                10,
                _secondary_text_color(),
            )
            summary = session.attention_summary or session.status_reason
            _draw_text(
                _truncate_to_width(summary, line_width, 10),
                18,
                79,
                10,
                AppKit.NSColor.whiteColor(),
            )
            arrow_color = (
                AppKit.NSColor.systemBlueColor()
                if session.terminal_session_id
                else _tertiary_text_color()
            )
            _draw_text(
                "↗",
                ATTENTION_WIDTH - 30,
                38,
                16,
                arrow_color,
                bold=True,
            )

        def _draw_expanded(self) -> None:
            title = "Agent Monitor · Paused" if self.paused else "Agent Monitor"
            _draw_text(
                title,
                18,
                13,
                14,
                AppKit.NSColor.whiteColor(),
                bold=True,
            )
            active, recent = _partition_sessions(
                self.sessions[:MAX_VISIBLE_SESSIONS]
            )
            count_text = f"{len(active)} active"
            _draw_text(
                count_text,
                OPEN_WIDTH - 70,
                14,
                11,
                _secondary_text_color(),
            )

            if not self.sessions:
                _draw_text(
                    "No recent Claude Code or Codex sessions",
                    18,
                    62,
                    12,
                    _secondary_text_color(),
                )
                return

            top = HEADER_HEIGHT
            for title, sessions in (("ACTIVE", active), ("RECENT", recent)):
                if not sessions:
                    continue
                _draw_text(
                    title,
                    18,
                    top + 7,
                    9,
                    _tertiary_text_color(),
                    bold=True,
                )
                top += SECTION_HEADER_HEIGHT
                for index, session in enumerate(sessions):
                    offset = (
                        self.swipe_offset
                        if _session_key(session) == self.swipe_key
                        else 0.0
                    )
                    self._draw_session(
                        session,
                        top,
                        separator=index > 0,
                        swipe_offset=offset,
                    )
                    top += ROW_HEIGHT

            selected = _selected_session(self.sessions, self.selected_key)
            if selected is not None:
                self._draw_details(selected, top)

        def _draw_session(
            self,
            session: AgentSession,
            top: float,
            *,
            separator: bool,
            swipe_offset: float = 0.0,
        ) -> None:
            if abs(swipe_offset) > SWIPE_REVEAL_DEADZONE:
                self._draw_swipe_reveal(top)
                return
            self._draw_session_content(session, top, separator=separator)

        def _draw_swipe_reveal(self, top: float) -> None:
            AppKit.NSColor.systemRedColor().set()
            path = (
                AppKit.NSBezierPath
                .bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((12, top + 2), (406, ROW_HEIGHT - 4)),
                    8.0,
                    8.0,
                )
            )
            path.fill()
            _draw_text(
                "Remove",
                190,
                top + 18,
                12,
                AppKit.NSColor.whiteColor(),
                bold=True,
            )

        def _draw_session_content(
            self,
            session: AgentSession,
            top: float,
            *,
            separator: bool,
        ) -> None:
            if _session_key(session) == self.selected_key:
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(
                    1.0,
                    0.08,
                ).set()
                _fill_rect(((12, top + 2), (406, ROW_HEIGHT - 4)))
            if separator:
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(
                    1.0,
                    0.08,
                ).set()
                _fill_rect(((18, top), (394, 1)))

            _status_color(session.status).set()
            _fill_oval(((18, top + 21), (8, 8)))

            provider = session.provider.value.upper()
            name = _truncate(
                _presented_name(session, self.terminal_names),
                28,
            )
            title = f"{name}  ·  {provider}"
            _draw_text(
                title[:44],
                36,
                top + 10,
                12,
                AppKit.NSColor.whiteColor(),
                bold=True,
            )

            detail_parts = [
                session.activity.value,
            ]
            if session.action:
                action = session.action.replace("\n", " ")[:20]
                if action.lower() != session.activity.value:
                    detail_parts.append(action)
            detail_parts.append(session.confidence.value)
            detail = " · ".join(detail_parts)
            detail_color = _secondary_text_color()
            if _session_key(session) == self.focus_failure_key:
                detail = self.focus_failure_message
                detail_color = AppKit.NSColor.systemOrangeColor()
            _draw_text(
                _truncate(detail, 48),
                36,
                top + 29,
                10,
                detail_color,
            )
            _draw_text(
                _age_label(session.age_seconds),
                342,
                top + 18,
                10,
                _tertiary_text_color(),
            )
            button_color = (
                AppKit.NSColor.systemBlueColor()
                if session.terminal_session_id
                else _tertiary_text_color()
            )
            button_color.colorWithAlphaComponent_(0.18).set()
            button = (
                AppKit.NSBezierPath
                .bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((TERMINAL_BUTTON_LEFT, top + 10), (38, 32)),
                    8.0,
                    8.0,
                )
            )
            button.fill()
            _draw_text(
                "↗",
                TERMINAL_BUTTON_LEFT + 12,
                top + 15,
                14,
                button_color,
                bold=True,
            )

        def _draw_details(
            self,
            session: AgentSession,
            top: float,
        ) -> None:
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.06).set()
            path = (
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((12, top + 4), (406, DETAIL_HEIGHT - 8)),
                    9.0,
                    9.0,
                )
            )
            path.fill()
            _draw_text(
                "DETAILS",
                22,
                top + 12,
                9,
                _tertiary_text_color(),
                bold=True,
            )
            details = _detail_lines(session, self.terminal_names)
            for index, line in enumerate(details):
                _draw_text(
                    _truncate(line, 60),
                    22,
                    top + 30 + (index * 18),
                    10,
                    (
                        AppKit.NSColor.whiteColor()
                        if index == 0
                        else _secondary_text_color()
                    ),
                    bold=index == 0,
                )
            detail_count = len(details)
            activity_top = top + 34 + (detail_count * 18)
            _draw_text(
                "RECENT ACTIVITY",
                22,
                activity_top,
                9,
                _tertiary_text_color(),
                bold=True,
            )
            timeline = _timeline_lines(session)
            if not timeline:
                timeline = ("No state changes observed yet",)
            for index, line in enumerate(timeline):
                _draw_text(
                    _truncate(line, 67),
                    22,
                    activity_top + 18 + (index * 18),
                    10,
                    _secondary_text_color(),
                )

    class NotchPanelController(NSObject):
        """Own and resize the floating AppKit panel."""

        def init(self) -> NotchPanelController:
            self = objc.super(NotchPanelController, self).init()  # noqa: PLW0642
            if self is None:
                return self

            style = (
                AppKit.NSWindowStyleMaskBorderless
                | AppKit.NSWindowStyleMaskNonactivatingPanel
            )
            self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                ((0, 0), (FALLBACK_CLOSED_WIDTH, CLOSED_HEIGHT)),
                style,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            self.panel.setLevel_(AppKit.NSMainMenuWindowLevel + 2)
            behavior = (
                AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            self.panel.setCollectionBehavior_(behavior)
            self.panel.setHidesOnDeactivate_(False)
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            self.panel.setHasShadow_(True)
            self.panel.setMovable_(False)

            self.animation_timer = None
            self.animation_started_at = 0.0
            self.animation_duration = 0.0
            self.animation_start_frame: PanelFrame | None = None
            self.animation_target_frame: PanelFrame | None = None
            self.animation_expanding = False
            self.animation_start_shell = 0.0
            self.animation_target_shell = 0.0

            self.view = SessionPanelView.alloc().initWithFrame_(
                ((0, 0), (FALLBACK_CLOSED_WIDTH, CLOSED_HEIGHT))
            )
            self.view.on_expansion = self.set_expanded
            self.panel.setContentView_(self.view)
            self._set_compact_frame()
            self.panel.orderFrontRegardless()
            return self

        def reposition(self) -> None:
            self._stop_animation()
            if self.view.expanded:
                frame = self.panel.frame()
                self._set_size(frame.size.width, frame.size.height)
            elif self.view.attention_session is not None:
                self._set_attention_frame()
            else:
                self._set_compact_frame()

        def show_expanded(self) -> None:
            self.view._set_expanded(True)
            self.panel.orderFrontRegardless()

        def set_sessions(self, sessions: list[AgentSession]) -> None:
            self.view.set_sessions(sessions)

        def set_terminal_names(self, names: dict[str, str]) -> None:
            self.view.set_terminal_names(names)

        def show_attention(self, session: AgentSession) -> None:
            self.view.show_attention(session)
            self.panel.orderFrontRegardless()

        def set_paused(self, paused: bool) -> None:
            self.view.set_paused(paused)

        def toggle_visible(self) -> bool:
            """Toggle panel visibility and return the new visible state."""

            if self.panel.isVisible():
                self.panel.orderOut_(None)
                return False
            self.panel.orderFrontRegardless()
            return True

        def set_expanded(self, expanded: bool) -> None:
            screen = self._target_screen()
            if screen is None:
                return
            if expanded:
                target = _expanded_panel_frame(
                    screen,
                    _expanded_height(
                        self.view.sessions,
                        self.view.selected_key,
                    ),
                )
                self.view.shell_kind = "expanded"
            elif self.view.attention_session is not None:
                target = _attention_panel_frame(screen)
                self.view.shell_kind = "attention"
            else:
                target = _compact_panel_frame(screen)
            target_shell = (
                1.0
                if expanded or self.view.attention_session is not None
                else 0.0
            )
            self._animate_to_frame(
                target,
                expanding=expanded,
                target_shell=target_shell,
            )

        def _animate_to_frame(
            self,
            target: PanelFrame,
            *,
            expanding: bool,
            target_shell: float,
        ) -> None:
            self._stop_animation()
            workspace = AppKit.NSWorkspace.sharedWorkspace()
            if _reduce_motion_enabled(workspace):
                self.view.shell_progress = target_shell
                self.panel.setFrame_display_(target, True)
                self.view.setNeedsDisplay_(True)
                return

            self.animation_start_frame = _frame_tuple(self.panel.frame())
            self.animation_target_frame = target
            self.animation_expanding = expanding
            self.animation_start_shell = self.view.shell_progress
            self.animation_target_shell = target_shell
            self.animation_duration = (
                EXPAND_DURATION if expanding else COLLAPSE_DURATION
            )
            self.animation_started_at = time.monotonic()
            self.animation_timer = (
                NSTimer
                .scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    ANIMATION_INTERVAL,
                    self,
                    "advancePanelAnimation:",
                    None,
                    True,
                )
            )
            self.advancePanelAnimation_(self.animation_timer)

        def advancePanelAnimation_(self, timer: Any) -> None:
            start = self.animation_start_frame
            target = self.animation_target_frame
            if start is None or target is None:
                self._stop_animation()
                return
            elapsed = time.monotonic() - self.animation_started_at
            progress = min(elapsed / self.animation_duration, 1.0)
            frame = _fluid_frame(
                start,
                target,
                progress,
                expanding=self.animation_expanding,
            )
            self.panel.setFrame_display_(frame, True)
            shell_progress = _ease_out_progress(progress)
            self.view.shell_progress = self.animation_start_shell + (
                (self.animation_target_shell - self.animation_start_shell)
                * shell_progress
            )
            self.view.setNeedsDisplay_(True)
            if progress >= 1.0:
                self.panel.setFrame_display_(target, True)
                self.view.shell_progress = self.animation_target_shell
                self.view.setNeedsDisplay_(True)
                self._stop_animation()

        def _stop_animation(self) -> None:
            if self.animation_timer is not None:
                self.animation_timer.invalidate()
                self.animation_timer = None

        def _target_screen(self) -> Any:
            screens = list(AppKit.NSScreen.screens())
            return _select_target_screen(
                screens,
                _screen_is_built_in,
                AppKit.NSScreen.mainScreen(),
            )

        def _set_compact_frame(self) -> None:
            screen = self._target_screen()
            if screen is None:
                return
            self.view.shell_progress = 0.0
            self.panel.setFrame_display_(
                _compact_panel_frame(screen),
                True,
            )

        def _set_attention_frame(self) -> None:
            screen = self._target_screen()
            if screen is None:
                return
            self.view.shell_progress = 1.0
            self.view.shell_kind = "attention"
            self.panel.setFrame_display_(
                _attention_panel_frame(screen),
                True,
            )

        def _set_size(self, width: float, height: float) -> None:
            screen = self._target_screen()
            if screen is None:
                return
            target = _expanded_panel_frame(screen, height)
            if width != OPEN_WIDTH:
                (x, y), (_, target_height) = target
                frame = screen.frame()
                x = frame.origin.x + ((frame.size.width - width) / 2)
                target = ((x, y), (width, target_height))
            self.view.shell_progress = 1.0
            self.view.shell_kind = "expanded"
            self.panel.setFrame_display_(target, True)

    class AgentMonitorDelegate(NSObject):
        """Start the panel and refresh local session state."""

        def initWithCoordinator_(self, coordinator: Any) -> Any:
            self = objc.super(AgentMonitorDelegate, self).init()  # noqa: PLW0642
            if self is None:
                return self
            self.coordinator = coordinator
            self.panel_controller = None
            self.timer = None
            self.status_item = None
            self.notification_manager = None
            self.notification_backend = None
            self.sound_backend = None
            self.notification_menu_item = None
            self.sound_menu_item = None
            self.sound_name_menu_items: dict[str, Any] = {}
            self.defaults = None
            self.terminal_focuser = TerminalFocuser()
            self.terminal_name_lookup = TerminalNameLookup()
            self.terminal_names: dict[str, str] = {}
            self.terminal_names_checked_at = 0.0
            self.paused = False
            self.latest_sessions: list[AgentSession] = []
            self.pause_menu_item = None
            self.panel_menu_item = None
            self.hotkey_manager = None
            return self

        def applicationDidFinishLaunching_(self, notification: Any) -> None:
            self.panel_controller = NotchPanelController.alloc().init()
            self.panel_controller.view.on_focus = self.focusTerminalSession
            self.notification_backend = (
                AppKitNotificationBackend.alloc().initWithActivationCallback_(
                    self.showNotificationSession_
                )
            )
            self.sound_backend = AppKitApprovalSoundBackend.alloc().init()
            self.defaults = NSUserDefaults.standardUserDefaults()
            sound_enabled = _stored_bool(
                self.defaults,
                APPROVAL_SOUNDS_DEFAULTS_KEY,
                default=True,
            )
            self.sound_backend.set_sound_name(
                _stored_sound_name(
                    self.defaults,
                    APPROVAL_SOUND_NAME_DEFAULTS_KEY,
                    default=DEFAULT_APPROVAL_SOUND_NAME,
                )
            )
            self.notification_manager = WaitingNotificationManager(
                self.notification_backend,
                sound_backend=self.sound_backend,
                sound_enabled=sound_enabled,
            )
            self._install_status_item()
            self.refresh_(None)
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.5,
                self,
                "refresh:",
                None,
                True,
            )
            self._install_global_hotkeys()

        @objc.python_method
        def _install_global_hotkeys(self) -> None:
            manager = GlobalHotKeyManager()
            if not manager.available:
                return
            manager.register(
                KEYCODE_A,
                CMD_KEY | OPTION_KEY,
                lambda: self.togglePanel_(None),
            )
            manager.register(
                KEYCODE_J,
                CMD_KEY | OPTION_KEY,
                lambda: self.jumpToLongestWaiting_(None),
            )
            manager.register(
                KEYCODE_P,
                CMD_KEY | OPTION_KEY,
                lambda: self.togglePaused_(None),
            )
            self.hotkey_manager = manager

        def applicationWillTerminate_(self, notification: Any) -> None:
            if self.hotkey_manager is not None:
                self.hotkey_manager.unregister_all()

        def refresh_(self, timer: Any) -> None:
            if not _should_refresh(
                paused=self.paused,
                timer_fired=timer is not None,
            ):
                return
            sessions = self.coordinator.refresh()
            self.latest_sessions = sessions
            if self.panel_controller is not None:
                self.panel_controller.set_sessions(sessions)
            if self.notification_manager is not None:
                new_waits = self.notification_manager.update(sessions)
                if new_waits and self.panel_controller is not None:
                    self.panel_controller.show_attention(new_waits[0])
            self._refresh_terminal_names(sessions)

        @objc.python_method
        def _refresh_terminal_names(
            self,
            sessions: list[AgentSession],
        ) -> None:
            now = time.monotonic()
            elapsed = now - self.terminal_names_checked_at
            if elapsed < TERMINAL_NAME_REFRESH_INTERVAL:
                return
            self.terminal_names_checked_at = now
            terminal_ids = [
                session.terminal_session_id
                for session in sessions
                if session.terminal_session_id
            ]
            self.terminal_names = self.terminal_name_lookup.names_for(
                terminal_ids
            )
            if self.panel_controller is not None:
                self.panel_controller.set_terminal_names(self.terminal_names)

        def showNotificationSession_(self, session: Any) -> None:
            if self.panel_controller is not None:
                self.panel_controller.show_expanded()

        @objc.python_method
        def focusTerminalSession(self, session: AgentSession) -> None:
            if self.terminal_focuser.focus(session):
                if self.panel_controller is not None:
                    self.panel_controller.view.clear_focus_failure()
                return
            if self.panel_controller is not None:
                self.panel_controller.view.show_focus_failure(session)
            AppKit.NSBeep()

        def toggleNotifications_(self, sender: Any) -> None:
            if self.notification_manager is None:
                return
            enabled = not self.notification_manager.enabled
            self.notification_manager.set_enabled(enabled)
            if self.notification_menu_item is not None:
                state = (
                    AppKit.NSControlStateValueOn
                    if enabled
                    else AppKit.NSControlStateValueOff
                )
                self.notification_menu_item.setState_(state)

        def toggleApprovalSounds_(self, sender: Any) -> None:
            if self.notification_manager is None:
                return
            enabled = not self.notification_manager.sound_enabled
            self.notification_manager.set_sound_enabled(enabled)
            if self.defaults is not None:
                self.defaults.setBool_forKey_(
                    enabled,
                    APPROVAL_SOUNDS_DEFAULTS_KEY,
                )
            if self.sound_menu_item is not None:
                state = (
                    AppKit.NSControlStateValueOn
                    if enabled
                    else AppKit.NSControlStateValueOff
                )
                self.sound_menu_item.setState_(state)

        def selectApprovalSound_(self, sender: Any) -> None:
            name = sender.representedObject()
            if name not in APPROVAL_SOUND_NAMES:
                return
            if self.sound_backend is not None:
                self.sound_backend.set_sound_name(name)
                self.sound_backend.play()
            if self.defaults is not None:
                self.defaults.setObject_forKey_(
                    name,
                    APPROVAL_SOUND_NAME_DEFAULTS_KEY,
                )
            for item_name, item in self.sound_name_menu_items.items():
                item.setState_(
                    AppKit.NSControlStateValueOn
                    if item_name == name
                    else AppKit.NSControlStateValueOff
                )

        def refreshNow_(self, sender: Any) -> None:
            self.refresh_(None)

        def jumpToLongestWaiting_(self, sender: Any) -> None:
            session = _longest_waiting_session(self.latest_sessions)
            if session is not None:
                self.focusTerminalSession(session)

        def togglePaused_(self, sender: Any) -> None:
            self.paused = not self.paused
            if self.panel_controller is not None:
                self.panel_controller.set_paused(self.paused)
            if self.pause_menu_item is not None:
                title = (
                    "Resume Monitoring (⌘⌥P)"
                    if self.paused
                    else "Pause Monitoring (⌘⌥P)"
                )
                self.pause_menu_item.setTitle_(title)
            if not self.paused:
                self.refresh_(None)

        def copyDebugSnapshot_(self, sender: Any) -> None:
            snapshot = format_debug_snapshot(self.latest_sessions)
            pasteboard = AppKit.NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setString_forType_(
                snapshot,
                AppKit.NSPasteboardTypeString,
            )

        def togglePanel_(self, sender: Any) -> None:
            if self.panel_controller is None:
                return
            visible = self.panel_controller.toggle_visible()
            if self.panel_menu_item is not None:
                title = (
                    "Hide Panel (⌘⌥A)" if visible else "Show Panel (⌘⌥A)"
                )
                self.panel_menu_item.setTitle_(title)

        def applicationDidChangeScreenParameters_(
            self,
            notification: Any,
        ) -> None:
            if self.panel_controller is not None:
                self.panel_controller.reposition()

        def _install_status_item(self) -> None:
            status_bar = AppKit.NSStatusBar.systemStatusBar()
            self.status_item = status_bar.statusItemWithLength_(
                AppKit.NSVariableStatusItemLength
            )
            button = self.status_item.button()
            if button is not None:
                icon = (
                    AppKit.NSImage
                    .imageWithSystemSymbolName_accessibilityDescription_(
                        "eye",
                        "Agent Monitor",
                    )
                )
                if icon is not None:
                    icon.setTemplate_(True)
                    button.setImage_(icon)
                else:
                    button.setTitle_("◉")

            menu = AppKit.NSMenu.alloc().init()
            title = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Agent Monitor",
                    None,
                    "",
                )
            )
            title.setEnabled_(False)
            menu.addItem_(title)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            refresh_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Refresh Now",
                    "refreshNow:",
                    "r",
                )
            )
            refresh_item.setTarget_(self)
            menu.addItem_(refresh_item)
            self.pause_menu_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Pause Monitoring (⌘⌥P)",
                    "togglePaused:",
                    "",
                )
            )
            self.pause_menu_item.setTarget_(self)
            menu.addItem_(self.pause_menu_item)
            copy_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Copy Debug Snapshot",
                    "copyDebugSnapshot:",
                    "",
                )
            )
            copy_item.setTarget_(self)
            menu.addItem_(copy_item)
            self.panel_menu_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Hide Panel (⌘⌥A)",
                    "togglePanel:",
                    "",
                )
            )
            self.panel_menu_item.setTarget_(self)
            menu.addItem_(self.panel_menu_item)
            jump_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Jump to Longest Waiting (⌘⌥J)",
                    "jumpToLongestWaiting:",
                    "",
                )
            )
            jump_item.setTarget_(self)
            menu.addItem_(jump_item)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self.notification_menu_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Notifications",
                    "toggleNotifications:",
                    "",
                )
            )
            self.notification_menu_item.setTarget_(self)
            self.notification_menu_item.setState_(
                AppKit.NSControlStateValueOn
            )
            menu.addItem_(self.notification_menu_item)
            self.sound_menu_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Approval Sounds",
                    "toggleApprovalSounds:",
                    "",
                )
            )
            self.sound_menu_item.setTarget_(self)
            sound_enabled = (
                self.notification_manager.sound_enabled
                if self.notification_manager is not None
                else True
            )
            self.sound_menu_item.setState_(
                AppKit.NSControlStateValueOn
                if sound_enabled
                else AppKit.NSControlStateValueOff
            )
            menu.addItem_(self.sound_menu_item)
            sound_choice_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Notification Sound",
                    None,
                    "",
                )
            )
            current_sound_name = (
                self.sound_backend.sound_name
                if self.sound_backend is not None
                else DEFAULT_APPROVAL_SOUND_NAME
            )
            sound_submenu = AppKit.NSMenu.alloc().init()
            self.sound_name_menu_items = {}
            for name in APPROVAL_SOUND_NAMES:
                item = (
                    AppKit.NSMenuItem
                    .alloc().initWithTitle_action_keyEquivalent_(
                        name,
                        "selectApprovalSound:",
                        "",
                    )
                )
                item.setTarget_(self)
                item.setRepresentedObject_(name)
                item.setState_(
                    AppKit.NSControlStateValueOn
                    if name == current_sound_name
                    else AppKit.NSControlStateValueOff
                )
                sound_submenu.addItem_(item)
                self.sound_name_menu_items[name] = item
            sound_choice_item.setSubmenu_(sound_submenu)
            menu.addItem_(sound_choice_item)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            quit_item = (
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Quit Agent Monitor",
                    "terminate:",
                    "q",
                )
            )
            menu.addItem_(quit_item)
            self.status_item.setMenu_(menu)

    class AppKitNotificationBackend(NSObject):
        """Deliver privacy-safe notifications through AppKit."""

        def initWithActivationCallback_(self, callback: Any) -> Any:
            self = objc.super(AppKitNotificationBackend, self).init()  # noqa: PLW0642
            if self is None:
                return self
            self.activation_callback = callback
            self.center = (
                AppKit.NSUserNotificationCenter.defaultUserNotificationCenter()
            )
            self.center.setDelegate_(self)
            return self

        @objc.python_method
        def notify(self, session: AgentSession) -> None:
            notification = AppKit.NSUserNotification.alloc().init()
            notification.setTitle_("Agent Monitor")
            notification.setSubtitle_(
                f"{session.display_name} · {session.provider.value.upper()}"
            )
            notification.setInformativeText_("Needs approval or input")
            notification.setUserInfo_(
                {
                    "provider": session.provider.value,
                    "session_id": session.session_id,
                }
            )
            self.center.deliverNotification_(notification)

        def userNotificationCenter_shouldPresentNotification_(
            self,
            center: Any,
            notification: Any,
        ) -> bool:
            return True

        def userNotificationCenter_didActivateNotification_(
            self,
            center: Any,
            notification: Any,
        ) -> None:
            if self.activation_callback is not None:
                self.activation_callback(notification.userInfo())

    class AppKitApprovalSoundBackend(NSObject):
        """Play a standard local sound for new approval requests."""

        def init(self) -> Any:
            self = objc.super(  # noqa: PLW0642
                AppKitApprovalSoundBackend,
                self,
            ).init()
            if self is None:
                return self
            self.sound_name = DEFAULT_APPROVAL_SOUND_NAME
            self.sound = AppKit.NSSound.soundNamed_(self.sound_name)
            return self

        @objc.python_method
        def set_sound_name(self, name: str) -> None:
            self.sound_name = name
            self.sound = AppKit.NSSound.soundNamed_(name)

        @objc.python_method
        def play(self) -> None:
            if self.sound is not None:
                self.sound.play()
            else:
                AppKit.NSBeep()


def _draw_text(
    text: str,
    x: float,
    y: float,
    size: float,
    color: Any,
    *,
    bold: bool = False,
) -> None:
    if AppKit is None or NSString is None:
        return
    weight = (
        AppKit.NSFontWeightSemibold if bold else AppKit.NSFontWeightRegular
    )
    font = AppKit.NSFont.systemFontOfSize_weight_(size, weight)
    attributes = {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: color,
    }
    NSString.stringWithString_(text).drawAtPoint_withAttributes_(
        (x, y),
        attributes,
    )


def _fill_oval(rect: Any) -> None:
    if AppKit is not None:
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(rect).fill()


def _fill_rect(rect: Any) -> None:
    if AppKit is not None:
        AppKit.NSBezierPath.bezierPathWithRect_(rect).fill()


def _status_color(status: SessionStatus | None) -> Any:
    if AppKit is None:
        return None
    colors = {
        SessionStatus.WAITING: AppKit.NSColor.systemRedColor(),
        SessionStatus.RUNNING: AppKit.NSColor.systemGreenColor(),
        SessionStatus.COMPLETE: AppKit.NSColor.systemBlueColor(),
        SessionStatus.IDLE: AppKit.NSColor.systemGrayColor(),
    }
    return colors.get(status, AppKit.NSColor.systemGrayColor())


def _secondary_text_color() -> Any:
    if AppKit is None:
        return None
    return AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.72, 1.0)


def _tertiary_text_color() -> Any:
    if AppKit is None:
        return None
    return AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.52, 1.0)


def _screen_is_built_in(screen: Any) -> bool:
    """Return whether an AppKit screen is the Mac's integrated display."""

    display_id = screen.deviceDescription().get("NSScreenNumber")
    if display_id is None:
        return False
    try:
        core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/"
            "CoreGraphics.framework/CoreGraphics"
        )
        is_built_in = core_graphics.CGDisplayIsBuiltin
        is_built_in.argtypes = [ctypes.c_uint32]
        is_built_in.restype = ctypes.c_uint32
        return bool(is_built_in(int(display_id)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


_delegate: Any = None


def run_app(coordinator: Any) -> None:
    """Run the accessory application event loop."""

    if AppKit is None:
        raise RuntimeError(
            "PyObjC is required for the macOS interface. "
            "Install the project with: python3 -m pip install -e ."
        )

    global _delegate
    application = AppKit.NSApplication.sharedApplication()
    application.setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )
    _delegate = AgentMonitorDelegate.alloc().initWithCoordinator_(coordinator)
    application.setDelegate_(_delegate)
    application.run()
