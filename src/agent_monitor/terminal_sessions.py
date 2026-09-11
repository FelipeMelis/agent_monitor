"""Privacy-safe terminal-session mappings captured by provider hooks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_monitor.models import AgentKind

MAPPING_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class TerminalSessionMapping:
    """Opaque iTerm session identity for one provider session."""

    provider: AgentKind
    session_id: str
    project: str
    cwd: str
    terminal_session_id: str
    updated_at: datetime


class TerminalSessionStore:
    """Persist minimal provider-to-iTerm mappings in Application Support."""

    def __init__(self, root: Path | None = None) -> None:
        configured_root = os.environ.get("AGENT_MONITOR_TERMINAL_DIR")
        self.root = root or (
            Path(configured_root)
            if configured_root
            else (
                Path.home()
                / "Library"
                / "Application Support"
                / "Agent Monitor"
                / "terminal-sessions"
            )
        )

    def record(
        self,
        provider: AgentKind,
        payload: dict[str, Any],
        *,
        environment: Mapping[str, str] | None = None,
        tty: str | None = None,
    ) -> None:
        """Record an opaque terminal ID without terminal or prompt content."""

        current_environment = (
            environment if environment is not None else os.environ
        )
        current_tty = tty if tty is not None else _current_tty()
        terminal_session_id = _terminal_session_id(
            current_environment,
            current_tty,
        )
        session_id = _string(
            payload,
            "session_id",
            "sessionId",
            "thread_id",
            "threadId",
        )
        if not terminal_session_id or not session_id:
            return
        cwd = _string(payload, "cwd")
        mapping = TerminalSessionMapping(
            provider=provider,
            session_id=session_id,
            project=Path(cwd).name if cwd else session_id[:8],
            cwd=cwd,
            terminal_session_id=terminal_session_id,
            updated_at=datetime.now(UTC),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(provider, session_id)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root,
            delete=False,
        ) as temporary:
            json.dump(
                _serialize_mapping(mapping),
                temporary,
                separators=(",", ":"),
            )
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
        self._remove_expired()

    def session_id_for(
        self,
        provider: AgentKind,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Return a recent iTerm session ID for one provider session."""

        try:
            payload = json.loads(
                self._path(provider, session_id).read_text(encoding="utf-8")
            )
            mapping = _deserialize_mapping(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ""
        current = now or datetime.now(UTC)
        if (
            mapping.provider is not provider
            or mapping.session_id != session_id
            or current - mapping.updated_at > MAPPING_TTL
        ):
            return ""
        return mapping.terminal_session_id

    def _remove_expired(self) -> None:
        cutoff = datetime.now(UTC) - MAPPING_TTL
        for mapping_file in self.root.glob("*.json"):
            try:
                modified = datetime.fromtimestamp(
                    mapping_file.stat().st_mtime,
                    tz=UTC,
                )
                if modified < cutoff:
                    mapping_file.unlink()
            except OSError:
                continue

    def _path(self, provider: AgentKind, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{provider.value}-{digest}.json"


def _current_tty() -> str:
    """Return the process's controlling terminal device, if any.

    The hook always reads its JSON payload from a piped stdin (and a
    caller may also redirect stdout/stderr for its own logging), so
    no single fd is reliably the terminal. `ps` reads the controlling
    terminal from the kernel's process table directly, independent of
    fd redirection.
    """

    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "tty=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    tty = result.stdout.strip()
    if not tty or tty == "??":
        return ""
    return f"/dev/{tty}"


def _terminal_session_id(environment: Mapping[str, str], tty: str) -> str:
    terminal_program = environment.get("TERM_PROGRAM", "")
    lowered = terminal_program.lower()
    if "iterm" in lowered:
        value = environment.get("ITERM_SESSION_ID") or environment.get(
            "TERM_SESSION_ID",
            "",
        )
        if _is_unsafe(value):
            return ""
        return f"iterm:{value}"
    if terminal_program == "Apple_Terminal":
        if _is_unsafe(tty):
            return ""
        return f"terminal:{tty}"
    return ""


def _is_unsafe(value: str) -> bool:
    return not value or len(value) > 256 or any(c in value for c in "\r\n")


def _string(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _serialize_mapping(mapping: TerminalSessionMapping) -> dict[str, str]:
    return {
        "provider": mapping.provider.value,
        "session_id": mapping.session_id,
        "project": mapping.project,
        "cwd": mapping.cwd,
        "terminal_session_id": mapping.terminal_session_id,
        "updated_at": mapping.updated_at.isoformat(),
    }


def _deserialize_mapping(payload: Any) -> TerminalSessionMapping:
    if not isinstance(payload, dict):
        raise TypeError("terminal mapping must be an object")
    return TerminalSessionMapping(
        provider=AgentKind(payload["provider"]),
        session_id=str(payload["session_id"]),
        project=str(payload["project"]),
        cwd=str(payload["cwd"]),
        terminal_session_id=str(payload["terminal_session_id"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
    )
