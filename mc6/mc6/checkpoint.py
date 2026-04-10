"""Taint checkpoint persistence for crash recovery.

Writes session state to ~/.mc6/sessions/<serial>.json at each
phase boundary. Deleted only on successful GO. A checkpoint left behind
after a crash forces the operator to acknowledge the incomplete session
and re-run from scratch.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from mc6 import session_log

_DEFAULT_SESSIONS_DIR = Path.home() / ".mc6" / "sessions"
_SESSIONS_DIR = _DEFAULT_SESSIONS_DIR


def write(serial: str, data: dict[str, Any]) -> None:
    """Write or overwrite a taint checkpoint for a device.

    Adds a 'started' timestamp if not already present.
    """
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    if "started" not in data:
        data["started"] = datetime.datetime.now().isoformat(timespec="seconds")

    path = _SESSIONS_DIR / f"{serial}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    session_log.log("checkpoint", f"Wrote checkpoint for {serial}: {data['phase_reached']}")


def read(serial: str) -> dict[str, Any] | None:
    """Read a taint checkpoint. Returns None if no checkpoint exists."""
    path = _SESSIONS_DIR / f"{serial}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def update(serial: str, updates: dict[str, Any]) -> None:
    """Update specific fields in an existing checkpoint.

    Preserves all existing fields (including 'started' timestamp).
    No-op if checkpoint doesn't exist.
    """
    data = read(serial)
    if data is None:
        return
    data.update(updates)

    path = _SESSIONS_DIR / f"{serial}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    session_log.log("checkpoint", f"Updated checkpoint for {serial}: {list(updates.keys())}")


def delete(serial: str) -> None:
    """Delete a taint checkpoint. Called only on successful GO."""
    path = _SESSIONS_DIR / f"{serial}.json"
    if path.exists():
        path.unlink()
        session_log.log("checkpoint", f"Deleted checkpoint for {serial} (GO)")


def scan_all() -> list[dict[str, Any]]:
    """Scan for all existing taint checkpoints.

    Returns list of checkpoint data dicts. Used at startup to detect
    incomplete sessions from previous runs.
    """
    if not _SESSIONS_DIR.exists():
        return []

    results: list[dict[str, Any]] = []
    for path in _SESSIONS_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return results
