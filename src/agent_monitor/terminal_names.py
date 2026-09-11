"""Look up current terminal session names for mapped terminal sessions.

Supports iTerm2 and Terminal.app, dispatching on the backend tag
`AgentSession.terminal_session_id` was captured with (see
`terminal_focus.py` for the tagging scheme).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable

ITERM_NAMES_SCRIPT = """on run argv
tell application "System Events"
if not (name of processes contains "iTerm2") then
return ""
end if
end tell
set tabChar to tab
set output to ""
tell application "iTerm2"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
repeat with aSession in sessions of aTab
set sid to id of aSession
if argv contains sid then
set output to output & sid & tabChar & (name of aSession) & linefeed
end if
end repeat
end repeat
end repeat
end tell
return output
end run"""

APPLE_TERMINAL_NAMES_SCRIPT = """on run argv
tell application "System Events"
if not (name of processes contains "Terminal") then
return ""
end if
end tell
set tabChar to tab
set output to ""
tell application "Terminal"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
set t to tty of aTab
if argv contains t then
set output to output & t & tabChar & (custom title of aTab) & linefeed
end if
end repeat
end repeat
end tell
return output
end run"""


class TerminalNameLookup:
    """Fetch current terminal session names without launching a terminal."""

    def __init__(
        self,
        iterm_backend: Callable[[list[str]], str] | None = None,
        apple_terminal_backend: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.iterm_backend = iterm_backend or _run_iterm_names_script
        self.apple_terminal_backend = (
            apple_terminal_backend or _run_apple_terminal_names_script
        )

    def names_for(
        self,
        terminal_session_ids: Iterable[str],
    ) -> dict[str, str]:
        """Return {terminal_session_id: current terminal session name}."""

        iterm_raw_to_original: dict[str, str] = {}
        terminal_raw_to_original: dict[str, str] = {}
        for terminal_session_id in dict.fromkeys(terminal_session_ids):
            if not terminal_session_id:
                continue
            backend, _, raw_id = terminal_session_id.partition(":")
            if backend == "iterm" and raw_id:
                match_id = raw_id.rsplit(":", maxsplit=1)[-1]
                iterm_raw_to_original[match_id] = terminal_session_id
            elif backend == "terminal" and raw_id:
                terminal_raw_to_original[raw_id] = terminal_session_id

        names: dict[str, str] = {}
        _merge_names(
            names,
            iterm_raw_to_original,
            self.iterm_backend,
        )
        _merge_names(
            names,
            terminal_raw_to_original,
            self.apple_terminal_backend,
        )
        return names


def _merge_names(
    names: dict[str, str],
    raw_to_original: dict[str, str],
    run_backend: Callable[[list[str]], str],
) -> None:
    if not raw_to_original:
        return
    raw_output = run_backend(list(raw_to_original))
    if not raw_output:
        return
    for line in raw_output.splitlines():
        if "\t" not in line:
            continue
        raw_id, name = line.split("\t", maxsplit=1)
        original = raw_to_original.get(raw_id)
        if original and name:
            names[original] = name


def _run_iterm_names_script(raw_ids: list[str]) -> str:
    return _run_names_script(ITERM_NAMES_SCRIPT, raw_ids)


def _run_apple_terminal_names_script(raw_ids: list[str]) -> str:
    return _run_names_script(APPLE_TERMINAL_NAMES_SCRIPT, raw_ids)


def _run_names_script(script: str, raw_ids: list[str]) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script, *raw_ids],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout
