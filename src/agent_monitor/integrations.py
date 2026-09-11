"""Provider-hook configuration previews and installation diagnostics."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from agent_monitor.models import AgentKind

CLAUDE_HOOK_EVENTS = {
    "SessionStart": "terminal_session",
    "PermissionRequest": "permission",
    "Notification": "notification",
    "PostToolUse": "post_tool",
    "PermissionDenied": "permission_denied",
    "UserPromptSubmit": "user_prompt",
    "SessionEnd": "session_end",
    "Stop": "stop",
}
CODEX_HOOK_EVENTS = {
    "SessionStart": "terminal_session",
    "PermissionRequest": "permission",
    "PostToolUse": "post_tool",
    "UserPromptSubmit": "user_prompt",
    "SessionEnd": "session_end",
    "Stop": "stop",
}


def hook_config(
    provider: AgentKind,
    *,
    executable: Path | None = None,
) -> dict[str, Any]:
    """Return a passive hook fragment without modifying user settings."""

    hook_executable = executable or _hook_executable()
    events = (
        CLAUDE_HOOK_EVENTS
        if provider is AgentKind.CLAUDE
        else CODEX_HOOK_EVENTS
    )
    hooks: dict[str, list[dict[str, Any]]] = {}
    for provider_event, bridge_event in events.items():
        command = " ".join(
            (
                shlex.quote(str(hook_executable)),
                provider.value,
                bridge_event,
            )
        )
        hooks[provider_event] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 5,
                    }
                ]
            }
        ]
    return {"hooks": hooks}


def format_hook_config(provider: AgentKind) -> str:
    """Format the exact fragment proposed for one provider."""

    return json.dumps(hook_config(provider), indent=2)


def format_integration_status() -> str:
    """Report whether each provider references the local hook bridge."""

    home = Path.home()
    paths = {
        AgentKind.CLAUDE: home / ".claude" / "settings.json",
        AgentKind.CODEX: home / ".codex" / "hooks.json",
    }
    lines = ["Agent Monitor live-hook integrations"]
    for provider, path in paths.items():
        configured = _contains_bridge(path, provider)
        state = "configured" if configured else "not configured"
        lines.append(f"{provider.value}: {state} · {path}")
    lines.append("codex command hooks must also be reviewed in /hooks")
    return "\n".join(lines)


def _hook_executable() -> Path:
    return Path(sys.executable).parent / "agent-monitor-hook"


def _contains_bridge(path: Path, provider: AgentKind) -> bool:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "agent-monitor-hook" in content and provider.value in content
