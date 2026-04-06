"""Session logging for Mercury config tool.

Every action, response, and error is appended to a timestamped log file.
This is the audit trail. If a flight goes wrong, we can prove what the tool
configured.
"""

from __future__ import annotations

import datetime
from pathlib import Path

_LOG_DIR = Path.home() / ".mercury-config" / "logs"

_log_file: Path | None = None
_log_handle = None


def init(serial: str | None = None) -> Path:
    """Initialise the session log. Returns the log file path.

    If serial is not yet known, uses 'unknown'. The log can be renamed
    later once we identify the device.
    """
    global _log_file, _log_handle

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    serial_slug = (serial or "unknown").replace(":", "")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file = _LOG_DIR / f"config_{serial_slug}_{ts}.log"
    _log_handle = open(_log_file, "a", encoding="utf-8")  # noqa: SIM115

    log("session", f"Session started — serial={serial or 'unknown'}")
    return _log_file


def rename_with_serial(serial: str) -> Path | None:
    """Rename the log file once serial is discovered. Returns new path."""
    global _log_file, _log_handle

    if _log_file is None or _log_handle is None:
        return None

    old_path = _log_file
    serial_slug = serial.replace(":", "")
    new_name = old_path.name.replace("unknown", serial_slug)
    new_path = old_path.parent / new_name

    if new_path == old_path:
        return old_path

    _log_handle.close()
    old_path.rename(new_path)
    _log_file = new_path
    _log_handle = open(_log_file, "a", encoding="utf-8")  # noqa: SIM115
    log("session", f"Log renamed: {old_path.name} -> {new_path.name}")
    return _log_file


def log(category: str, message: str) -> None:
    """Append a timestamped line to the session log."""
    if _log_handle is None:
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    _log_handle.write(f"[{ts}] [{category:12s}] {message}\n")
    _log_handle.flush()


def log_config_field(name: str, value: str, expected: str | None, status: str) -> None:
    """Log a config field comparison."""
    if expected is not None:
        log("config", f"{status:8s} {name:25s} = {value:15s} (expected: {expected})")
    else:
        log("config", f"{status:8s} {name:25s} = {value}")


def log_raw(label: str, data: str) -> None:
    """Log raw data (HTTP responses, serial output, etc.)."""
    log("raw", f"--- {label} ---")
    for line in data.splitlines():
        log("raw", f"  {line}")
    log("raw", f"--- end {label} ---")


def close() -> None:
    """Flush and close the log file."""
    global _log_handle
    if _log_handle is not None:
        log("session", "Session ended")
        _log_handle.flush()
        _log_handle.close()
        _log_handle = None


def get_path() -> Path | None:
    """Return the current log file path, or None."""
    return _log_file
