"""Managed device list — JSON persistence at ~/.mercury-config/devices.json.

Tracks known Mercurys by MAC address. Stores SSID, revision, firmware, and
last-configured timestamp so we can identify devices on subsequent runs.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import TypedDict

_DEVICES_PATH = Path.home() / ".mercury-config" / "devices.json"

# The managed password for all FC6 Mercurys
MANAGED_PASSWORD = "05c69008"


class DeviceRecord(TypedDict):
    ssid: str
    revision: int
    firmware: str
    last_configured: str


def load() -> dict[str, DeviceRecord]:
    """Load the managed devices list. Returns empty dict if file doesn't exist."""
    if not _DEVICES_PATH.exists():
        return {}
    try:
        with open(_DEVICES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save(devices: dict[str, DeviceRecord]) -> None:
    """Save the managed devices list."""
    _DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_DEVICES_PATH, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)
        f.write("\n")


def lookup(serial: str) -> DeviceRecord | None:
    """Look up a device by serial (MAC). Returns None if not found."""
    devices = load()
    return devices.get(serial)


def is_managed(serial: str) -> bool:
    """Check if a device is in the managed list."""
    return serial in load()


def adopt(
    serial: str,
    ssid: str,
    revision: int,
    firmware: str,
) -> None:
    """Add or update a device in the managed list."""
    devices = load()
    devices[serial] = DeviceRecord(
        ssid=ssid,
        revision=revision,
        firmware=firmware,
        last_configured=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    save(devices)


def update_timestamp(serial: str) -> None:
    """Update the last-configured timestamp for a known device."""
    devices = load()
    if serial in devices:
        devices[serial]["last_configured"] = (
            datetime.datetime.now().isoformat(timespec="seconds")
        )
        save(devices)


def get_ssid_for_serial(serial: str) -> str | None:
    """Return the stored SSID for a serial, or None."""
    record = lookup(serial)
    if record is not None:
        return record.get("ssid")
    return None
