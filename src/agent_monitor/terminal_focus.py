"""Focus the terminal session associated with an agent session.

Supports iTerm2 and Terminal.app. `AgentSession.terminal_session_id`
is tagged with which backend captured it (e.g. "iterm:w0t1p0:ABC" or
"terminal:/dev/ttys001"), so focusing routes to the right one.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from urllib.parse import quote

from agent_monitor.models import AgentSession

ITERM_FOCUS_SCRIPT = """on run argv
set targetId to item 1 of argv
tell application "iTerm2"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
repeat with aSession in sessions of aTab
if id of aSession is targetId then
select aSession
select aTab
select aWindow
activate
return "focused"
end if
end repeat
end repeat
end repeat
end tell
return "missing"
end run"""

APPLE_TERMINAL_FOCUS_SCRIPT = """on run argv
set targetTty to item 1 of argv
tell application "Terminal"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
if tty of aTab is targetTty then
set selected tab of aWindow to aTab
set index of aWindow to 1
activate
return "focused"
end if
end repeat
end repeat
end tell
return "missing"
end run"""


class TerminalFocuser:
    """Focus an exact terminal session without reading terminal content."""

    def __init__(
        self,
        iterm_backend: Callable[[str], bool] | None = None,
        apple_terminal_backend: Callable[[str], bool] | None = None,
    ) -> None:
        self.iterm_backend = iterm_backend or _focus_iterm_session
        self.apple_terminal_backend = (
            apple_terminal_backend or _focus_apple_terminal_session
        )

    def focus(self, session: AgentSession) -> bool:
        """Focus the mapped terminal session and report whether it launched."""

        backend, _, raw_id = session.terminal_session_id.partition(":")
        if not raw_id:
            return False
        if backend == "iterm":
            return self.iterm_backend(raw_id)
        if backend == "terminal":
            return self.apple_terminal_backend(raw_id)
        return False


def _focus_iterm_session(raw_id: str) -> bool:
    target_id = raw_id.rsplit(":", maxsplit=1)[-1]
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                ITERM_FOCUS_SCRIPT,
                target_id,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if (
        result is not None
        and result.returncode == 0
        and result.stdout.strip() == "focused"
    ):
        return True
    return _open_reveal_url(raw_id)


def _open_reveal_url(raw_id: str) -> bool:
    encoded = quote(raw_id, safe="")
    url = f"iterm2:///reveal?sessionid={encoded}"
    try:
        result = subprocess.run(
            ["/usr/bin/open", "-a", "iTerm", url],
            check=False,
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _focus_apple_terminal_session(tty: str) -> bool:
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                APPLE_TERMINAL_FOCUS_SCRIPT,
                tty,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "focused"
