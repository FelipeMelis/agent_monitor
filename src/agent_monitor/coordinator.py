"""Combine provider monitors into one session feed."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_monitor.live_events import LiveAttentionEvent, LiveEventStore
from agent_monitor.models import (
    ActivityKind,
    AgentKind,
    AgentSession,
    EvidenceConfidence,
    SessionStatus,
    StateSource,
    StateTransition,
    session_sort_key,
)
from agent_monitor.monitors import ClaudeMonitor, CodexMonitor
from agent_monitor.monitors.base import JSONLMonitor
from agent_monitor.terminal_sessions import TerminalSessionStore

EVIDENCE_AGING_SECONDS = 120


class SessionCoordinator:
    """Refresh all providers and order sessions by required attention."""

    def __init__(
        self,
        monitors: list[JSONLMonitor] | None = None,
        *,
        feed_window: timedelta = timedelta(minutes=15),
        max_sessions: int = 7,
        max_transitions: int = 5,
        event_store: LiveEventStore | None = None,
        terminal_store: TerminalSessionStore | None = None,
    ) -> None:
        self.monitors = monitors or self.default_monitors()
        self.feed_window = feed_window
        self.max_sessions = max_sessions
        self.max_transitions = max_transitions
        self.event_store = event_store or LiveEventStore()
        self.terminal_store = terminal_store or TerminalSessionStore()
        self._last_states: dict[
            tuple[str, str],
            tuple[SessionStatus, ActivityKind, EvidenceConfidence],
        ] = {}
        self._transitions: dict[
            tuple[str, str], deque[StateTransition]
        ] = {}

    @staticmethod
    def default_monitors() -> list[JSONLMonitor]:
        """Create monitors for the standard local agent directories."""

        home = Path.home()
        return [
            ClaudeMonitor(home / ".claude" / "projects"),
            CodexMonitor(home / ".codex" / "sessions"),
        ]

    def refresh(self) -> list[AgentSession]:
        """Discover, combine and prioritize sessions."""

        sessions = [
            session
            for monitor in self.monitors
            for session in monitor.discover()
        ]
        sessions = _merge_live_events(
            sessions,
            self.event_store.active_events(),
        )
        sessions = [self._attach_terminal(session) for session in sessions]
        sessions = [_apply_evidence(session) for session in sessions]
        sessions = self._record_transitions(sessions)
        sessions = [
            session
            for session in sessions
            if session.age_seconds <= self.feed_window.total_seconds()
        ]
        sessions.sort(key=session_sort_key)
        return sessions[: self.max_sessions]

    def _attach_terminal(self, session: AgentSession) -> AgentSession:
        terminal_session_id = self.terminal_store.session_id_for(
            session.provider,
            session.session_id,
        )
        return replace(
            session,
            terminal_session_id=terminal_session_id,
        )

    def _record_transitions(
        self,
        sessions: list[AgentSession],
    ) -> list[AgentSession]:
        """Attach bounded state history to the currently discovered sessions."""

        observed_at = datetime.now(UTC)
        active_keys = {_session_key(session) for session in sessions}
        for key in set(self._last_states) - active_keys:
            self._last_states.pop(key, None)
            self._transitions.pop(key, None)

        enriched = []
        for session in sessions:
            key = _session_key(session)
            current = (
                session.status,
                session.activity,
                session.confidence,
            )
            previous = self._last_states.get(key)
            history = self._transitions.setdefault(
                key,
                deque(maxlen=self.max_transitions),
            )
            if previous != current:
                history.appendleft(
                    StateTransition(
                        from_status=previous[0] if previous else None,
                        to_status=session.status,
                        from_activity=(
                            previous[1] if previous else None
                        ),
                        to_activity=session.activity,
                        occurred_at=observed_at,
                        reason=(
                            session.status_reason
                            or "no classification reason recorded"
                        ),
                        source=session.evidence_source,
                        confidence=session.confidence,
                    )
                )
                self._last_states[key] = current
            enriched.append(replace(session, transitions=tuple(history)))
        return enriched


def _merge_live_events(
    sessions: list[AgentSession],
    events: list[LiveAttentionEvent],
) -> list[AgentSession]:
    """Prefer explicit hook events over inferred transcript state."""

    merged = list(sessions)
    for event in events:
        index = _matching_session_index(merged, event)
        if index is None:
            merged.append(
                AgentSession(
                    session_id=event.session_id,
                    provider=event.provider,
                    status=SessionStatus.WAITING,
                    project=event.project,
                    last_activity=event.updated_at,
                    transcript_path=Path(),
                    action=event.tool_name or "Needs attention",
                    status_reason="an explicit provider hook is unresolved",
                    attention_kind=event.kind,
                    attention_summary=event.summary,
                    activity=ActivityKind.WAITING_APPROVAL,
                )
            )
            continue
        session = merged[index]
        if _transcript_resolves_event(session, event):
            continue
        merged[index] = replace(
            session,
            status=SessionStatus.WAITING,
            last_activity=max(session.last_activity, event.updated_at),
            action=event.tool_name or "Needs attention",
            status_reason="an explicit provider hook is unresolved",
            attention_kind=event.kind,
            attention_summary=event.summary,
            activity=ActivityKind.WAITING_APPROVAL,
        )
    return merged


def _matching_session_index(
    sessions: list[AgentSession],
    event: LiveAttentionEvent,
) -> int | None:
    if event.session_id:
        for index, session in enumerate(sessions):
            if (
                session.provider is event.provider
                and session.session_id == event.session_id
            ):
                return index
    candidates = [
        (index, session)
        for index, session in enumerate(sessions)
        if session.provider is event.provider
        and session.project == event.project
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: item[1].last_activity,
    )[0]


def _session_key(session: AgentSession) -> tuple[str, str]:
    return session.provider.value, session.session_id


def _transcript_resolves_event(
    session: AgentSession,
    event: LiveAttentionEvent,
) -> bool:
    """Let newer Codex command-start evidence resolve an approval wait."""

    return (
        session.provider is AgentKind.CODEX
        and session.activity is ActivityKind.RUNNING_COMMAND
        and session.status is SessionStatus.RUNNING
        and session.last_activity > event.updated_at
    )


def _apply_evidence(session: AgentSession) -> AgentSession:
    source = (
        StateSource.HOOK
        if "explicit provider hook" in session.status_reason
        else StateSource.TRANSCRIPT
    )
    if session.age_seconds >= EVIDENCE_AGING_SECONDS:
        confidence = EvidenceConfidence.AGING
    elif source is StateSource.HOOK:
        confidence = EvidenceConfidence.CONFIRMED
    else:
        confidence = EvidenceConfidence.INFERRED
    return replace(
        session,
        evidence_source=source,
        confidence=confidence,
    )
