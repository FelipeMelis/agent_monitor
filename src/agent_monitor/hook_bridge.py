"""Fail-open command entry point for Claude and Codex lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import suppress
from typing import TextIO

from agent_monitor.live_events import LiveEventStore
from agent_monitor.models import AgentKind
from agent_monitor.terminal_sessions import TerminalSessionStore


def main(argv: list[str] | None = None) -> None:
    """Accept one hook payload and never alter the provider's decision."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("provider", choices=[kind.value for kind in AgentKind])
    parser.add_argument("event")
    arguments = parser.parse_args(argv)
    try:
        payload = _read_payload(sys.stdin)
        provider = AgentKind(arguments.provider)
        with suppress(Exception):
            TerminalSessionStore().record(
                provider,
                payload,
                environment=os.environ,
            )
        if arguments.event != "terminal_session":
            LiveEventStore().record(
                provider,
                arguments.event,
                payload,
            )
    except Exception:  # noqa: BLE001 - provider hooks must always fail open
        return


def _read_payload(stream: TextIO) -> dict[str, object]:
    payload = json.load(stream)
    if not isinstance(payload, dict):
        raise TypeError("hook payload must be an object")
    return payload


if __name__ == "__main__":
    main()
