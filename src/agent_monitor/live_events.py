"""Sanitized live attention events emitted by provider hooks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_monitor.models import AgentKind, AttentionKind

EVENT_TTL = timedelta(hours=6)
RELEVANT_NOTIFICATIONS = {
    "elicitation_dialog",
    "permission_prompt",
}
CLEAR_NOTIFICATIONS = {"idle_prompt"}
CLEAR_EVENTS = {
    "clear",
    "permission_denied",
    "post_tool",
    "session_end",
    "stop",
    "user_prompt",
}


@dataclass(frozen=True, slots=True)
class LiveAttentionEvent:
    """Minimal local event used to override transcript-derived state."""

    provider: AgentKind
    session_id: str
    project: str
    cwd: str
    kind: AttentionKind
    tool_name: str
    summary: str
    updated_at: datetime


class LiveEventStore:
    """Persist hook events atomically in the user's application data."""

    def __init__(self, root: Path | None = None) -> None:
        configured_root = os.environ.get("AGENT_MONITOR_EVENT_DIR")
        self.root = root or (
            Path(configured_root)
            if configured_root
            else (
                Path.home()
                / "Library"
                / "Application Support"
                / "Agent Monitor"
                / "events"
            )
        )

    def record(
        self,
        provider: AgentKind,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Record or clear one event without retaining prompt content."""

        identity = _event_identity(payload)
        if event_name in CLEAR_EVENTS:
            self._clear(provider, identity)
            return

        notification = _string(
            payload,
            "notification_type",
            "notificationType",
        )
        if event_name == "notification" and notification in (
            CLEAR_NOTIFICATIONS
        ):
            self._clear(provider, identity)
            return
        if (
            event_name == "notification"
            and notification not in RELEVANT_NOTIFICATIONS
        ):
            return

        event = _normalize_event(provider, event_name, payload)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(provider, identity)
        serialized = _serialize_event(event)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root,
            delete=False,
        ) as temporary:
            json.dump(serialized, temporary, separators=(",", ":"))
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)

    def active_events(
        self,
        *,
        now: datetime | None = None,
    ) -> list[LiveAttentionEvent]:
        """Return recent valid events and ignore malformed local files."""

        if not self.root.is_dir():
            return []
        current = now or datetime.now(UTC)
        events: list[LiveAttentionEvent] = []
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                event = _deserialize_event(payload)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if current - event.updated_at <= EVENT_TTL:
                events.append(event)
        return events

    def _clear(self, provider: AgentKind, identity: str) -> None:
        try:
            self._path(provider, identity).unlink()
        except FileNotFoundError:
            pass

    def _path(self, provider: AgentKind, identity: str) -> Path:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{provider.value}-{digest}.json"


def _event_identity(payload: dict[str, Any]) -> str:
    session_id = _string(
        payload,
        "session_id",
        "sessionId",
        "thread_id",
        "threadId",
    )
    if session_id:
        return session_id
    cwd = _string(payload, "cwd")
    return cwd or "unknown-session"


def _normalize_event(
    provider: AgentKind,
    event_name: str,
    payload: dict[str, Any],
) -> LiveAttentionEvent:
    cwd = _string(payload, "cwd")
    session_id = _string(
        payload,
        "session_id",
        "sessionId",
        "thread_id",
        "threadId",
    )
    tool_name = _string(
        payload,
        "tool_name",
        "toolName",
        "name",
    )
    notification = _string(
        payload,
        "notification_type",
        "notificationType",
    )
    kind = _attention_kind(tool_name, notification, event_name)
    project = Path(cwd).name if cwd else session_id[:8]
    return LiveAttentionEvent(
        provider=provider,
        session_id=session_id,
        project=project,
        cwd=cwd,
        kind=kind,
        tool_name=tool_name,
        summary=_attention_summary(provider, kind, tool_name),
        updated_at=datetime.now(UTC),
    )


def _attention_kind(
    tool_name: str,
    notification: str,
    event_name: str,
) -> AttentionKind:
    normalized = tool_name.lower()
    if normalized in {
        "apply_patch",
        "create",
        "edit",
        "multiedit",
        "notebookedit",
        "write",
    }:
        return AttentionKind.EDIT
    if normalized in {"bash", "exec", "exec_command", "shell"}:
        return AttentionKind.COMMAND
    if normalized in {"ask_user", "askuserquestion", "request_user_input"}:
        return AttentionKind.INPUT
    if notification == "elicitation_dialog":
        return AttentionKind.INPUT
    if event_name == "input":
        return AttentionKind.INPUT
    return AttentionKind.PERMISSION


def _attention_summary(
    provider: AgentKind,
    kind: AttentionKind,
    tool_name: str,
) -> str:
    label = tool_name or kind.value
    return f"{provider.value.title()} requests {label} access"


def _string(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _serialize_event(event: LiveAttentionEvent) -> dict[str, str]:
    return {
        "provider": event.provider.value,
        "session_id": event.session_id,
        "project": event.project,
        "cwd": event.cwd,
        "kind": event.kind.value,
        "tool_name": event.tool_name,
        "summary": event.summary,
        "updated_at": event.updated_at.isoformat(),
    }


def _deserialize_event(payload: Any) -> LiveAttentionEvent:
    if not isinstance(payload, dict):
        raise TypeError("event must be an object")
    return LiveAttentionEvent(
        provider=AgentKind(payload["provider"]),
        session_id=str(payload["session_id"]),
        project=str(payload["project"]),
        cwd=str(payload["cwd"]),
        kind=AttentionKind(payload["kind"]),
        tool_name=str(payload["tool_name"]),
        summary=str(payload["summary"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
    )
