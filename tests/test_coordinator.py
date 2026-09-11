"""Tests for combining provider sessions into a focused live feed."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_monitor.coordinator import SessionCoordinator
from agent_monitor.live_events import LiveAttentionEvent
from agent_monitor.models import (
    ActivityKind,
    AgentKind,
    AgentSession,
    AttentionKind,
    EvidenceConfidence,
    SessionStatus,
)
from agent_monitor.monitors.base import JSONLMonitor


class StaticMonitor(JSONLMonitor):
    def __init__(self, sessions: list[AgentSession]) -> None:
        self.sessions = sessions

    def discover(self) -> list[AgentSession]:
        return self.sessions

    def parse_session(self, path: Path) -> AgentSession:
        raise NotImplementedError

    def set_sessions(self, sessions: list[AgentSession]) -> None:
        self.sessions = sessions


class StaticEventStore:
    def __init__(self, events: list[LiveAttentionEvent]) -> None:
        self.events = events

    def active_events(self) -> list[LiveAttentionEvent]:
        return self.events


class StaticTerminalStore:
    def __init__(self, terminal_session_id: str) -> None:
        self.terminal_session_id = terminal_session_id

    def session_id_for(
        self,
        provider: AgentKind,
        session_id: str,
    ) -> str:
        return self.terminal_session_id


def make_session(name: str, age: timedelta) -> AgentSession:
    return AgentSession(
        session_id=name,
        provider=AgentKind.CODEX,
        status=SessionStatus.IDLE,
        project=name,
        last_activity=datetime.now(UTC) - age,
        transcript_path=Path(f"{name}.jsonl"),
    )


class SessionCoordinatorTests(unittest.TestCase):
    def test_terminal_mapping_is_attached_to_session(self) -> None:
        session = make_session("project", timedelta(minutes=2))
        coordinator = SessionCoordinator(
            [StaticMonitor([session])],
            event_store=StaticEventStore([]),
            terminal_store=StaticTerminalStore("w0t1p0:ABC"),
        )

        observed = coordinator.refresh()[0]

        self.assertEqual(observed.terminal_session_id, "w0t1p0:ABC")

    def test_old_sessions_are_removed_from_live_feed(self) -> None:
        monitor = StaticMonitor(
            [
                make_session("recent", timedelta(minutes=2)),
                make_session("old", timedelta(hours=2)),
            ]
        )
        coordinator = SessionCoordinator(
            [monitor],
            feed_window=timedelta(minutes=15),
            event_store=StaticEventStore([]),
        )

        sessions = coordinator.refresh()

        self.assertEqual([session.project for session in sessions], ["recent"])

    def test_live_event_overrides_transcript_inference(self) -> None:
        session = make_session("project", timedelta(minutes=2))
        event = LiveAttentionEvent(
            provider=AgentKind.CODEX,
            session_id=session.session_id,
            project=session.project,
            cwd="/Users/example/project",
            kind=AttentionKind.COMMAND,
            tool_name="bash",
            summary="Codex requests bash access",
            updated_at=datetime.now(UTC),
        )
        coordinator = SessionCoordinator(
            [StaticMonitor([session])],
            event_store=StaticEventStore([event]),
        )

        sessions = coordinator.refresh()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].status, SessionStatus.WAITING)
        self.assertEqual(sessions[0].attention_kind, AttentionKind.COMMAND)
        self.assertIn("explicit provider hook", sessions[0].status_reason)
        self.assertEqual(sessions[0].transitions[0].source.value, "hook")
        self.assertEqual(
            sessions[0].confidence,
            EvidenceConfidence.CONFIRMED,
        )

    def test_newer_codex_command_start_resolves_hook_wait(self) -> None:
        event_time = datetime.now(UTC) - timedelta(seconds=2)
        session = replace(
            make_session("project", timedelta(seconds=1)),
            status=SessionStatus.RUNNING,
            activity=ActivityKind.RUNNING_COMMAND,
            status_reason=(
                "an approved command reported that it is still running"
            ),
        )
        event = LiveAttentionEvent(
            provider=AgentKind.CODEX,
            session_id=session.session_id,
            project=session.project,
            cwd="/Users/example/project",
            kind=AttentionKind.COMMAND,
            tool_name="Bash",
            summary="Codex requests Bash access",
            updated_at=event_time,
        )
        coordinator = SessionCoordinator(
            [StaticMonitor([session])],
            event_store=StaticEventStore([event]),
        )

        observed = coordinator.refresh()[0]

        self.assertEqual(observed.status, SessionStatus.RUNNING)
        self.assertEqual(observed.activity, ActivityKind.RUNNING_COMMAND)
        self.assertEqual(
            observed.confidence,
            EvidenceConfidence.INFERRED,
        )

    def test_older_codex_tool_call_does_not_clear_hook_wait(self) -> None:
        session = replace(
            make_session("project", timedelta(seconds=2)),
            status=SessionStatus.RUNNING,
            activity=ActivityKind.RUNNING_COMMAND,
        )
        event = LiveAttentionEvent(
            provider=AgentKind.CODEX,
            session_id=session.session_id,
            project=session.project,
            cwd="/Users/example/project",
            kind=AttentionKind.COMMAND,
            tool_name="Bash",
            summary="Codex requests Bash access",
            updated_at=datetime.now(UTC),
        )
        coordinator = SessionCoordinator(
            [StaticMonitor([session])],
            event_store=StaticEventStore([event]),
        )

        observed = coordinator.refresh()[0]

        self.assertEqual(observed.status, SessionStatus.WAITING)
        self.assertEqual(observed.confidence, EvidenceConfidence.CONFIRMED)

    def test_old_transcript_evidence_is_marked_as_aging(self) -> None:
        session = make_session("project", timedelta(minutes=3))
        coordinator = SessionCoordinator(
            [StaticMonitor([session])],
            event_store=StaticEventStore([]),
        )

        observed = coordinator.refresh()[0]

        self.assertEqual(observed.confidence, EvidenceConfidence.AGING)
        self.assertEqual(observed.evidence_source.value, "transcript")

    def test_activity_change_is_recorded_without_status_change(self) -> None:
        running = replace(
            make_session("project", timedelta(seconds=5)),
            status=SessionStatus.RUNNING,
            activity=ActivityKind.THINKING,
        )
        monitor = StaticMonitor([running])
        coordinator = SessionCoordinator(
            [monitor],
            event_store=StaticEventStore([]),
        )
        coordinator.refresh()
        monitor.set_sessions(
            [replace(running, activity=ActivityKind.RUNNING_COMMAND)]
        )

        changed = coordinator.refresh()[0]

        self.assertEqual(len(changed.transitions), 2)
        latest = changed.transitions[0]
        self.assertEqual(latest.from_status, SessionStatus.RUNNING)
        self.assertEqual(latest.to_status, SessionStatus.RUNNING)
        self.assertEqual(latest.from_activity, ActivityKind.THINKING)
        self.assertEqual(
            latest.to_activity,
            ActivityKind.RUNNING_COMMAND,
        )

    def test_state_changes_are_recorded_without_duplicates(self) -> None:
        idle = make_session("project", timedelta(minutes=2))
        monitor = StaticMonitor([idle])
        coordinator = SessionCoordinator(
            [monitor],
            event_store=StaticEventStore([]),
        )

        first = coordinator.refresh()[0]
        unchanged = coordinator.refresh()[0]
        monitor.set_sessions(
            [
                replace(
                    idle,
                    status=SessionStatus.RUNNING,
                    status_reason="a tool call is awaiting its output",
                )
            ]
        )
        changed = coordinator.refresh()[0]

        self.assertEqual(len(first.transitions), 1)
        self.assertEqual(len(unchanged.transitions), 1)
        self.assertEqual(len(changed.transitions), 2)
        latest = changed.transitions[0]
        self.assertEqual(latest.from_status, SessionStatus.IDLE)
        self.assertEqual(latest.to_status, SessionStatus.RUNNING)
        self.assertEqual(latest.source.value, "transcript")

    def test_transition_history_is_bounded(self) -> None:
        session = make_session("project", timedelta(minutes=2))
        monitor = StaticMonitor([session])
        coordinator = SessionCoordinator(
            [monitor],
            max_transitions=2,
            event_store=StaticEventStore([]),
        )

        coordinator.refresh()
        for status in (
            SessionStatus.RUNNING,
            SessionStatus.WAITING,
            SessionStatus.COMPLETE,
        ):
            session = replace(
                session,
                status=status,
                transitions=(),
            )
            monitor.set_sessions([session])
            latest = coordinator.refresh()[0]

        self.assertEqual(len(latest.transitions), 2)
        self.assertEqual(
            [item.to_status for item in latest.transitions],
            [SessionStatus.COMPLETE, SessionStatus.WAITING],
        )


if __name__ == "__main__":
    unittest.main()
