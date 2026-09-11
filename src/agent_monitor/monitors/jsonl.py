"""Utilities shared by JSONL transcript readers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_TAIL_BYTES = 1_048_576
MAX_HEAD_BYTES = 131_072


def iter_jsonl_head(
    path: Path,
    max_lines: int = 100,
) -> Iterator[dict[str, Any]]:
    """Yield valid JSON objects from the start of a JSONL file.

    Session metadata is commonly written once at the beginning of a transcript
    and can fall outside the bounded tail used for live-state parsing.
    """

    with path.open("rb") as transcript:
        data = transcript.read(MAX_HEAD_BYTES)

    if len(data) == MAX_HEAD_BYTES:
        last_newline = data.rfind(b"\n")
        data = data[:last_newline] if last_newline >= 0 else b""

    yield from _parse_jsonl_lines(data.splitlines()[:max_lines])


def iter_jsonl_tail(
    path: Path,
    max_lines: int = 250,
) -> Iterator[dict[str, Any]]:
    """Yield valid JSON objects from the tail of a JSONL file.

    Transcripts can become very large. Reading at most one MiB keeps the UI
    refresh predictable while still preserving the recent session state.
    """

    with path.open("rb") as transcript:
        transcript.seek(0, 2)
        end = transcript.tell()
        start = max(0, end - MAX_TAIL_BYTES)
        transcript.seek(start)
        data = transcript.read()

    if start:
        first_newline = data.find(b"\n")
        data = data[first_newline + 1 :] if first_newline >= 0 else b""

    yield from _parse_jsonl_lines(data.splitlines()[-max_lines:])


def _parse_jsonl_lines(
    lines: list[bytes],
) -> Iterator[dict[str, Any]]:
    """Decode JSON objects from a bounded collection of raw lines."""

    for raw_line in lines:
        try:
            value = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            yield value


def parse_timestamp(value: Any, fallback: datetime) -> datetime:
    """Parse common JSONL timestamps and always return a UTC datetime."""

    if not isinstance(value, str) or not value:
        return fallback

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return fallback

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def file_modified_at(path: Path) -> datetime:
    """Return a transcript modification time in UTC."""

    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def project_from_cwd(cwd: str, fallback: str) -> str:
    """Choose a human-friendly project label from a working directory."""

    if cwd:
        name = Path(cwd).name
        if name:
            return name

    decoded = fallback.strip("-")
    if "-" in decoded:
        return decoded.rsplit("-", maxsplit=1)[-1]
    return decoded or "Unknown project"
