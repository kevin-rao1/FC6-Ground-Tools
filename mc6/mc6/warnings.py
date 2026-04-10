"""Flight-safety warning registry.

Every off-nominal condition that must be reviewed before GO passes through
register(). The Phase 9 review gate calls get_all() to replay each warning
and demand ACCEPT.

All flight-safety warnings are findable with: grep warnings.register
"""

from __future__ import annotations

from mc6 import session_log
from mc6 import ui

_MAX_WARNINGS = 64

_warnings: list[tuple[str, str]] = []


def register(category: str, message: str) -> None:
    """Register a flight-safety warning.

    Immediately prints via ui.warn() and logs to session log.
    Stored for replay at Phase 9 Flight Readiness Review.
    """
    if len(_warnings) >= _MAX_WARNINGS:
        session_log.log("warning", f"Warning overflow — limit {_MAX_WARNINGS} reached, not registering: [{category}] {message}")
        return
    ui.warn(message)
    session_log.log("warning", f"[{category}] {message}")
    _warnings.append((category, message))


def get_all() -> list[tuple[str, str]]:
    """Return all registered warnings as (category, message) tuples."""
    return list(_warnings)


def count() -> int:
    """Return the number of registered warnings."""
    return len(_warnings)


def clear() -> None:
    """Clear all warnings. Used on session reset."""
    _warnings.clear()


def serialise() -> list[dict[str, str]]:
    """Serialise warnings for taint checkpoint persistence."""
    return [
        {"category": cat, "message": msg}
        for cat, msg in _warnings
    ]


def deserialise(data: list[dict[str, str]]) -> None:
    """Load warnings from checkpoint data. Does NOT print or log —
    these are historical warnings from a previous crashed session.

    Replaces any existing warnings. Atomic: either all entries load
    or none do. Raises ValueError on malformed data.
    """
    restored: list[tuple[str, str]] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or "category" not in entry or "message" not in entry:
            raise ValueError(f"Malformed warning at index {i}: {entry!r}")
        restored.append((entry["category"], entry["message"]))
    _warnings.clear()
    _warnings.extend(restored)
