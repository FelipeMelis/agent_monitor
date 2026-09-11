"""Claude Code JSONL session monitor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_monitor.models import (
    ActivityKind,
    AgentKind,
    AgentSession,
    AttentionKind,
    SessionStatus,
)
from agent_monitor.monitors.base import JSONLMonitor
from agent_monitor.monitors.jsonl import (
    file_modified_at,
    iter_jsonl_tail,
    parse_timestamp,
    project_from_cwd,
)

WAITING_RECORD_TYPES = {
    "approval_request",
    "approval-request",
    "permission_request",
    "permission-request",
}


def _content_blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured content blocks from common Claude records."""

    message = record.get("message")
    content: Any = None
    if isinstance(message, dict):
        content = message.get("content")
    if content is None:
        content = record.get("content")
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


class ClaudeMonitor(JSONLMonitor):
    """Read local Claude Code project transcripts."""

    def transcript_paths(self) -> list[Path]:
        """Return top-level sessions without duplicating subagent logs."""

        return [
            path
            for path in self.root.rglob("*.jsonl")
            if "subagents" not in path.parts
        ]

    def parse_session(self, path: Path) -> AgentSession:
        """Parse recent Claude messages into a compact live state."""

        modified_at = file_modified_at(path)
        last_activity = modified_at
        active_tools: dict[str, str] = {}
        completed = False
        explicit_wait = False
        attention_kind: AttentionKind | None = None
        attention_summary = ""
        cwd = ""
        model = ""
        branch = ""
        action = ""
        token_count = 0

        for record in iter_jsonl_tail(path):
            timestamp = parse_timestamp(record.get("timestamp"), modified_at)
            last_activity = max(last_activity, timestamp)
            turn_ended = False

            record_cwd = record.get("cwd")
            if isinstance(record_cwd, str):
                cwd = record_cwd
            record_branch = record.get("gitBranch")
            if isinstance(record_branch, str):
                branch = record_branch

            message = record.get("message")
            if isinstance(message, dict):
                message_model = message.get("model")
                if isinstance(message_model, str):
                    model = message_model

                usage = message.get("usage")
                if isinstance(usage, dict):
                    token_count += _usage_tokens(usage)

                stop_reason = message.get("stop_reason")
                if stop_reason == "end_turn":
                    turn_ended = True

            record_type = str(record.get("type", "")).lower()
            if record_type in WAITING_RECORD_TYPES:
                explicit_wait = True
                attention_kind, attention_summary = (
                    _explicit_attention(record)
                )
            elif record_type == "user":
                explicit_wait = False
                attention_kind = None
                attention_summary = ""

            for block in _content_blocks(record):
                block_type = str(block.get("type", ""))
                if block_type == "tool_use":
                    tool_id = str(block.get("id", ""))
                    tool_name = str(block.get("name", "Tool"))
                    if tool_id:
                        active_tools[tool_id] = tool_name
                    action = tool_name
                    completed = False
                    explicit_wait = False
                    attention_kind = None
                    attention_summary = ""
                elif block_type == "tool_result":
                    tool_id = str(block.get("tool_use_id", ""))
                    active_tools.pop(tool_id, None)
                    explicit_wait = False
                    attention_kind = None
                    attention_summary = ""
                    action = "Processing result"
                elif block_type == "thinking":
                    action = "Thinking"
                    completed = False
                    explicit_wait = False
                    attention_kind = None
                    attention_summary = ""
                elif block_type == "text" and not completed:
                    action = "Responding"
                    explicit_wait = False
                    attention_kind = None
                    attention_summary = ""

            if record.get("toolUseResult") is not None:
                explicit_wait = False
                attention_kind = None
                attention_summary = ""

            if turn_ended and not active_tools:
                completed = True
                explicit_wait = False
                action = "Finished"
                attention_kind = None
                attention_summary = ""

        if active_tools:
            action = next(reversed(active_tools.values()))

        age = (datetime.now(UTC) - last_activity).total_seconds()
        status, status_reason = _claude_state(
            active_tools=active_tools,
            explicit_wait=explicit_wait,
            completed=completed,
            age=age,
        )
        activity = _claude_activity(
            status=status,
            action=action,
            active_tools=active_tools,
        )

        return AgentSession(
            session_id=path.stem,
            provider=AgentKind.CLAUDE,
            status=status,
            project=project_from_cwd(cwd, path.parent.name),
            last_activity=last_activity,
            transcript_path=path,
            action=action,
            model=model,
            branch=branch,
            token_count=token_count,
            status_reason=status_reason,
            activity=activity,
            attention_kind=(
                attention_kind
                if status is SessionStatus.WAITING
                else None
            ),
            attention_summary=(
                attention_summary
                if status is SessionStatus.WAITING
                else ""
            ),
        )


def _explicit_attention(
    record: dict[str, Any],
) -> tuple[AttentionKind, str]:
    """Extract a safe description from a Claude permission record."""

    tool_name = record.get("tool_name", record.get("toolName"))
    tool_input = record.get("tool_input", record.get("toolInput"))
    if not isinstance(tool_input, dict):
        tool_input = {}
    if isinstance(tool_name, str):
        return _tool_attention(tool_name, tool_input)
    return AttentionKind.PERMISSION, "Review the requested access"


def _tool_attention(
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[AttentionKind, str]:
    """Map a Claude tool request to a provider-neutral attention reason."""

    if tool_name in {"Edit", "MultiEdit", "Write", "NotebookEdit"}:
        path = tool_input.get("file_path", tool_input.get("notebook_path"))
        summary = path if isinstance(path, str) else "Review file changes"
        return AttentionKind.EDIT, summary
    if tool_name == "Bash":
        command = tool_input.get("command")
        summary = command if isinstance(command, str) else "Review command"
        return AttentionKind.COMMAND, summary
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        summary = url if isinstance(url, str) else "Review network access"
        return AttentionKind.PERMISSION, summary
    return AttentionKind.PERMISSION, f"Review {tool_name} access"


def _usage_tokens(usage: dict[str, Any]) -> int:
    """Return visible and cached tokens from a Claude usage object."""

    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    return sum(
        value for key in keys if isinstance(value := usage.get(key), int)
    )


def _claude_state(
    *,
    active_tools: dict[str, str],
    explicit_wait: bool,
    completed: bool,
    age: float,
) -> tuple[SessionStatus, str]:
    """Resolve Claude state and provide a privacy-safe explanation."""

    if age > 300:
        return (
            SessionStatus.IDLE,
            "last transcript activity is older than 5 minutes",
        )
    if explicit_wait:
        return (
            SessionStatus.WAITING,
            "an approval or permission event is unresolved",
        )
    if active_tools:
        return (
            SessionStatus.RUNNING,
            "a tool call is awaiting its result",
        )
    if completed:
        return SessionStatus.COMPLETE, "the assistant turn ended"
    if age <= 12:
        return SessionStatus.RUNNING, "transcript activity is recent"
    return (
        SessionStatus.IDLE,
        "there is no active tool or completed assistant turn",
    )


def _claude_activity(
    *,
    status: SessionStatus,
    action: str,
    active_tools: dict[str, str],
) -> ActivityKind:
    """Normalize Claude's current action for compact display."""

    if status is SessionStatus.WAITING:
        return ActivityKind.WAITING_APPROVAL
    if status is SessionStatus.COMPLETE:
        return ActivityKind.FINISHED
    if status is SessionStatus.IDLE:
        return ActivityKind.IDLE
    if active_tools:
        tool_name = next(reversed(active_tools.values()))
        if tool_name == "Bash":
            return ActivityKind.RUNNING_COMMAND
        return ActivityKind.RUNNING_TOOL
    return {
        "Thinking": ActivityKind.THINKING,
        "Processing result": ActivityKind.PROCESSING_RESULT,
        "Responding": ActivityKind.RESPONDING,
    }.get(action, ActivityKind.ACTIVE)
