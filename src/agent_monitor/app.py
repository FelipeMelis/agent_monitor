"""Agent Monitor application entry point."""

from __future__ import annotations

import argparse
import sys
import time
from typing import TextIO

from agent_monitor.coordinator import SessionCoordinator
from agent_monitor.debug import debug_signature, format_debug_snapshot
from agent_monitor.integrations import (
    format_hook_config,
    format_integration_status,
)
from agent_monitor.models import AgentKind
from agent_monitor.ui.notch_panel import run_app


def main(argv: list[str] | None = None) -> None:
    """Start the local monitors and macOS panel."""

    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.watch and not arguments.debug:
        parser.error("--watch requires --debug")

    if arguments.hooks_status:
        print(format_integration_status())
        return
    if arguments.print_hook_config:
        print(format_hook_config(AgentKind(arguments.print_hook_config)))
        return

    coordinator = SessionCoordinator()
    if arguments.debug:
        _run_debug(coordinator, watch=arguments.watch)
        return

    try:
        run_app(coordinator)
    except RuntimeError as error:
        print(f"Agent Monitor: {error}", file=sys.stderr)
        raise SystemExit(1) from error


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor local Claude Code and Codex sessions.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print a privacy-safe session-state snapshot and exit",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep printing debug snapshots when classifications change",
    )
    parser.add_argument(
        "--hooks-status",
        action="store_true",
        help="report passive live-hook integration status and exit",
    )
    parser.add_argument(
        "--print-hook-config",
        choices=[kind.value for kind in AgentKind],
        help="print the proposed passive hook fragment and exit",
    )
    return parser


def _run_debug(
    coordinator: SessionCoordinator,
    *,
    watch: bool,
    output: TextIO = sys.stdout,
) -> None:
    previous_signature: tuple[tuple[str, ...], ...] | None = None
    try:
        while True:
            sessions = coordinator.refresh()
            signature = debug_signature(sessions)
            if signature != previous_signature:
                print(format_debug_snapshot(sessions), file=output)
                output.flush()
                previous_signature = signature
            if not watch:
                return
            time.sleep(1.5)
    except KeyboardInterrupt:
        return
if __name__ == "__main__":
    main()
