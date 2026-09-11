"""Shared models for agent session monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class AgentKind(StrEnum):
    """Supported coding-agent providers."""

    CLAUDE = "claude"
    CODEX = "codex"


class SessionStatus(StrEnum):
    """Small set of states shared by every provider."""

    WAITING = "waiting"
    RUNNING = "running"
    COMPLETE = "complete"
    IDLE = "idle"


class AttentionKind(StrEnum):
    """Provider-neutral reason an agent needs the user's attention."""

    EDIT = "edit"
    COMMAND = "command"
    PERMISSION = "permission"
    INPUT = "input"


class StateSource(StrEnum):
    """Privacy-safe source used to derive a session state."""

    HOOK = "hook"
    TRANSCRIPT = "transcript"


class EvidenceConfidence(StrEnum):
    """How directly and recently Agent Monitor observed a state."""

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    AGING = "aging"


class ActivityKind(StrEnum):
    """Provider-neutral description of what an agent is doing."""

    ACTIVE = "active"
    THINKING = "thinking"
    RUNNING_COMMAND = "running command"
    RUNNING_TOOL = "running tool"
    PROCESSING_RESULT = "processing result"
    RESPONDING = "responding"
    WAITING_APPROVAL = "waiting approval"
    FINISHED = "finished"
    IDLE = "idle"


STATUS_PRIORITY = {
    SessionStatus.WAITING: 0,
    SessionStatus.RUNNING: 1,
    SessionStatus.COMPLETE: 2,
    SessionStatus.IDLE: 3,
}


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One observed session-state change without transcript content."""

    from_status: SessionStatus | None
    to_status: SessionStatus
    from_activity: ActivityKind | None
    to_activity: ActivityKind
    occurred_at: datetime
    reason: str
    source: StateSource
    confidence: EvidenceConfidence


@dataclass(frozen=True, slots=True)
class AgentSession:
    """Provider-neutral representation of one coding-agent session."""

    session_id: str
    provider: AgentKind
    status: SessionStatus
    project: str
    last_activity: datetime
    transcript_path: Path
    action: str = ""
    model: str = ""
    branch: str = ""
    token_count: int = 0
    status_reason: str = ""
    attention_kind: AttentionKind | None = None
    attention_summary: str = ""
    activity: ActivityKind = ActivityKind.ACTIVE
    evidence_source: StateSource = StateSource.TRANSCRIPT
    confidence: EvidenceConfidence = EvidenceConfidence.INFERRED
    transitions: tuple[StateTransition, ...] = ()
    terminal_session_id: str = ""

    @property
    def age_seconds(self) -> float:
        """Return the session age relative to the current UTC time."""

        return max(
            0.0,
            (datetime.now(UTC) - self.last_activity).total_seconds(),
        )

    @property
    def display_name(self) -> str:
        """Return a compact project name suitable for the notch panel."""

        return self.project or self.session_id[:8]


def session_sort_key(session: AgentSession) -> tuple[int, float]:
    """Sort urgent sessions first and newest sessions within each state."""

    return (
        STATUS_PRIORITY[session.status],
        -session.last_activity.timestamp(),
    )
