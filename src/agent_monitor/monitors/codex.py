"""OpenAI Codex JSONL session monitor."""

from __future__ import annotations

import json
import re
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
    iter_jsonl_head,
    iter_jsonl_tail,
    parse_timestamp,
    project_from_cwd,
)

WAITING_EVENT_TYPES = {
    "apply_patch_approval_request",
    "exec_approval_request",
    "request_user_input",
}

CALL_TYPES = {"function_call", "custom_tool_call"}
CALL_OUTPUT_TYPES = {"function_call_output", "custom_tool_call_output"}


class CodexMonitor(JSONLMonitor):
    """Read local Codex rollout transcripts."""

    def parse_session(self, path: Path) -> AgentSession:
        """Parse recent Codex events into a provider-neutral state."""

        modified_at = file_modified_at(path)
        last_activity = modified_at
        active_calls: dict[str, str] = {}
        background_command = False
        waiting = False
        attention_kind: AttentionKind | None = None
        attention_summary = ""
        completed = False
        cwd = ""
        model = ""
        branch = ""
        action = ""
        token_count = 0
        session_id = path.stem

        for record in iter_jsonl_head(path):
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            session_id = _string(payload.get("id"), session_id)
            cwd = _string(payload.get("cwd"), cwd)
            branch = _git_branch(payload, branch)

        for record in iter_jsonl_tail(path):
            timestamp = parse_timestamp(record.get("timestamp"), modified_at)
            last_activity = max(last_activity, timestamp)
            record_type = str(record.get("type", ""))
            payload = record.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            payload_type = str(payload.get("type", ""))

            if record_type == "session_meta":
                session_id = str(payload.get("id", session_id))
                cwd = _string(payload.get("cwd"), cwd)
                branch = _git_branch(payload, branch)
            elif record_type == "turn_context":
                cwd = _string(payload.get("cwd"), cwd)
                model = _string(payload.get("model"), model)
            elif record_type == "response_item":
                if payload_type in CALL_TYPES:
                    call_id = _string(payload.get("call_id"), "")
                    name = _string(payload.get("name"), "Tool")
                    if call_id:
                        active_calls[call_id] = name
                    action = name
                    waiting = (
                        name in WAITING_EVENT_TYPES
                        or _requires_escalation(payload)
                    )
                    if waiting:
                        event_type = (
                            name
                            if name in WAITING_EVENT_TYPES
                            else "exec_approval_request"
                        )
                        attention_kind, attention_summary = (
                            _codex_attention(
                                event_type,
                                _call_arguments(payload),
                            )
                        )
                    completed = False
                elif payload_type in CALL_OUTPUT_TYPES:
                    call_id = _string(payload.get("call_id"), "")
                    call_name = active_calls.pop(call_id, "")
                    if (
                        _is_command_call(call_name)
                        and _output_reports_running(payload)
                    ):
                        background_command = True
                        action = "Command running"
                    else:
                        if call_name.lower().endswith("write_stdin"):
                            background_command = False
                        action = "Processing result"
                    waiting = False
                    attention_kind = None
                    attention_summary = ""
                    completed = False
                elif payload_type == "reasoning":
                    action = "Thinking"
                    waiting = False
                    attention_kind = None
                    attention_summary = ""
                    completed = False
                elif payload_type == "message":
                    role = _string(payload.get("role"), "")
                    if role == "assistant" and not active_calls:
                        action = "Finished"
                        completed = True
                        background_command = False
                        waiting = False
                        attention_kind = None
                        attention_summary = ""
            elif record_type == "event_msg":
                if payload_type in WAITING_EVENT_TYPES:
                    waiting = True
                    completed = False
                    action = _waiting_action(payload)
                    attention_kind, attention_summary = _codex_attention(
                        payload_type,
                        payload,
                    )
                elif payload_type == "agent_reasoning":
                    waiting = False
                    attention_kind = None
                    attention_summary = ""
                    completed = False
                    action = "Thinking"
                elif payload_type == "agent_message":
                    waiting = False
                    attention_kind = None
                    attention_summary = ""
                    if not active_calls:
                        action = "Responding"
                        completed = False
                elif payload_type in {"task_complete", "turn_complete"}:
                    waiting = False
                    completed = True
                    background_command = False
                    action = "Finished"
                    attention_kind = None
                    attention_summary = ""
                elif payload_type == "token_count":
                    token_count = _codex_tokens(payload, token_count)

        if active_calls:
            action = next(reversed(active_calls.values()))

        age = (datetime.now(UTC) - last_activity).total_seconds()
        status, status_reason = _codex_state(
            waiting=waiting,
            active_calls=active_calls,
            background_command=background_command,
            completed=completed,
            age=age,
        )
        activity = _codex_activity(
            status=status,
            action=action,
            active_calls=active_calls,
            background_command=background_command,
        )

        return AgentSession(
            session_id=session_id,
            provider=AgentKind.CODEX,
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


def _string(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) else fallback


def _git_branch(payload: dict[str, Any], fallback: str) -> str:
    git = payload.get("git")
    if isinstance(git, dict):
        return _string(git.get("branch"), fallback)
    return fallback


def _waiting_action(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, str) and command:
        return command
    return "Needs your input"


def _call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _requires_escalation(payload: dict[str, Any]) -> bool:
    """Detect a pending Codex escalation without retaining its command."""

    for key in ("arguments", "input"):
        value = payload.get(key)
        if isinstance(value, dict):
            if value.get("sandbox_permissions") == "require_escalated":
                return True
            continue
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if (
            isinstance(parsed, dict)
            and parsed.get("sandbox_permissions") == "require_escalated"
        ):
            return True
        if re.search(
            r'''["']?sandbox_permissions["']?\s*:\s*'''
            r'''["']require_escalated["']''',
            value,
        ):
            return True
    return False


def _output_reports_running(payload: dict[str, Any]) -> bool:
    """Detect Codex's structural marker for a yielded live command."""

    output = payload.get("output")
    if not isinstance(output, str):
        return False
    return any(
        marker in output
        for marker in (
            "Script running with cell ID",
            "Process running with session ID",
        )
    )


def _is_command_call(call_name: str) -> bool:
    normalized = call_name.lower()
    return any(
        part in normalized for part in ("bash", "command", "exec")
    )


def _codex_attention(
    event_type: str,
    payload: dict[str, Any],
) -> tuple[AttentionKind, str]:
    """Extract a concise description from a Codex attention event."""

    if event_type == "apply_patch_approval_request":
        summary = _first_string(
            payload,
            "reason",
            "file_path",
            "path",
        )
        return AttentionKind.EDIT, summary or "Review proposed file changes"
    if event_type == "exec_approval_request":
        command = _first_string(payload, "command", "reason")
        return AttentionKind.COMMAND, command or "Review command"
    question = _first_string(payload, "question", "prompt")
    questions = payload.get("questions")
    if not question and isinstance(questions, list) and questions:
        first = questions[0]
        if isinstance(first, dict):
            question = _first_string(first, "question", "header")
    return AttentionKind.INPUT, question or "Review the requested input"


def _first_string(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _codex_tokens(payload: dict[str, Any], fallback: int) -> int:
    info = payload.get("info")
    if not isinstance(info, dict):
        return fallback
    usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        return fallback
    total = usage.get("total_tokens")
    return total if isinstance(total, int) else fallback


def _codex_state(
    *,
    waiting: bool,
    active_calls: dict[str, str],
    background_command: bool,
    completed: bool,
    age: float,
) -> tuple[SessionStatus, str]:
    if age > 300:
        return (
            SessionStatus.IDLE,
            "last transcript activity is older than 5 minutes",
        )
    if waiting:
        return (
            SessionStatus.WAITING,
            "an approval or user-input event is unresolved",
        )
    if background_command:
        return (
            SessionStatus.RUNNING,
            "an approved command reported that it is still running",
        )
    if active_calls:
        return (
            SessionStatus.RUNNING,
            "a tool call is awaiting its output",
        )
    if completed:
        return SessionStatus.COMPLETE, "the assistant turn completed"
    if age <= 8:
        return SessionStatus.RUNNING, "transcript activity is recent"
    return (
        SessionStatus.IDLE,
        "there is no active call or completed assistant turn",
    )


def _codex_activity(
    *,
    status: SessionStatus,
    action: str,
    active_calls: dict[str, str],
    background_command: bool,
) -> ActivityKind:
    """Normalize Codex's current action for compact display."""

    if status is SessionStatus.WAITING:
        return ActivityKind.WAITING_APPROVAL
    if status is SessionStatus.COMPLETE:
        return ActivityKind.FINISHED
    if status is SessionStatus.IDLE:
        return ActivityKind.IDLE
    if background_command:
        return ActivityKind.RUNNING_COMMAND
    if active_calls:
        call_name = next(reversed(active_calls.values())).lower()
        if any(part in call_name for part in ("exec", "bash", "command")):
            return ActivityKind.RUNNING_COMMAND
        return ActivityKind.RUNNING_TOOL
    return {
        "Thinking": ActivityKind.THINKING,
        "Processing result": ActivityKind.PROCESSING_RESULT,
        "Responding": ActivityKind.RESPONDING,
    }.get(action, ActivityKind.ACTIVE)
