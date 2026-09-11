"""Privacy-safe formatting for CLI and clipboard diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_monitor.models import AgentSession


def debug_signature(
    sessions: list[AgentSession],
) -> tuple[tuple[str, ...], ...]:
    """Return the state fields that should trigger a watch update."""

    return tuple(
        (
            session.session_id,
            session.status.value,
            session.action,
            session.status_reason,
            session.activity.value,
            session.confidence.value,
            session.evidence_source.value,
            _latest_transition_signature(session),
        )
        for session in sessions
    )


def format_debug_snapshot(sessions: list[AgentSession]) -> str:
    """Format derived session state without paths or message content."""

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [f"Agent Monitor debug snapshot · {timestamp}"]
    if not sessions:
        lines.append("No sessions in the current feed.")
        return "\n".join(lines)

    for session in sessions:
        action = session.action.replace("\n", " ") or "none"
        lines.append(
            " | ".join(
                (
                    session.provider.value,
                    session.display_name,
                    session.status.value,
                    f"activity={session.activity.value}",
                    f"age={int(session.age_seconds)}s",
                    f"action={action}",
                    f"reason={session.status_reason or 'not recorded'}",
                    (
                        f"evidence={session.confidence.value}/"
                        f"{session.evidence_source.value}"
                    ),
                )
            )
        )
    return "\n".join(lines)


def _latest_transition_signature(session: AgentSession) -> str:
    if not session.transitions:
        return ""
    transition = session.transitions[0]
    return (
        f"{transition.to_status.value}|"
        f"{transition.occurred_at.isoformat()}|"
        f"{transition.source.value}|"
        f"{transition.to_activity.value}|"
        f"{transition.confidence.value}"
    )
