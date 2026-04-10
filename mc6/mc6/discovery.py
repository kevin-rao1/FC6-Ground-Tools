"""Phase 0-1: Environment checks and USB device discovery.

Checks Python dependencies, serial port access, nmcli availability, WiFi
hardware, and scans for Mercury devices by USB VID:PID.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from mc6 import session_log
from mc6 import ui

# ESP32-C6 USB-Serial/JTAG VID:PID
MERCURY_VID = "303a"
MERCURY_PID = "1001"

DISCOVERY_TIMEOUT_S = 30
DISCOVERY_POLL_INTERVAL_S = 1.0
DEVICE_SETTLE_MS = 500
MAX_VANISH_RETRIES = 3


class EnvironmentError(Exception):
    """Raised when the environment is not suitable for operation."""


class DiscoveryError(Exception):
    """Raised when device discovery fails."""


def check_python_deps() -> list[str]:
    """Check that required Python packages are importable.

    Returns list of missing package names (empty = all OK).
    """
    missing: list[str] = []
    for module_name, pip_name in [("serial", "pyserial"), ("requests", "requests")]:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def check_serial_access() -> bool:
    """Check if the current user can likely access serial ports.

    On Arch Linux, user needs to be in the 'uucp' group.
    We check group membership rather than trying to open a port (which
    would require a device to be connected).
    """
    try:
        groups_output = subprocess.run(
            ["groups"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return "uucp" in groups_output.split()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_nmcli() -> bool:
    """Check that nmcli is available."""
    return shutil.which("nmcli") is not None


def check_wifi_hardware() -> str | None:
    """Check WiFi radio status via nmcli.

    Returns:
        "enabled" if WiFi radio is on.
        "disabled" if WiFi radio is off (can be enabled).
        None if no WiFi adapter found or nmcli failed.
    """
    try:
        result = subprocess.run(
            ["nmcli", "radio", "wifi"],
            capture_output=True, text=True, timeout=15,
        )
        status = result.stdout.strip().lower()
        if status == "enabled":
            return "enabled"
        elif status == "disabled":
            return "disabled"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_current_wifi_connection() -> str | None:
    """Get the name of the currently active WiFi connection.

    Returns the connection name, or None if not connected.
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1]:
                return parts[0]
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def check_internet(timeout_s: int = 3) -> bool:
    """Quick internet connectivity check. Best-effort, non-critical."""
    import requests
    try:
        requests.head("https://www.google.com", timeout=timeout_s)
        return True
    except Exception:
        return False


def run_environment_checks(manual_wifi: bool = False) -> tuple[bool, str | None]:
    """Run all Phase 0 environment checks.

    Args:
        manual_wifi: If True, skip nmcli checks.

    Returns:
        (internet_available, previous_wifi_connection_name)

    Raises:
        EnvironmentError: If a critical check fails.
    """
    session_log.log("env", "Starting environment checks")

    # 0.1 — Python deps
    missing = check_python_deps()
    if missing:
        for pkg in missing:
            ui.error(f"Missing Python package: {pkg}")
            ui.info(f"  Install: pip install {pkg}")
        session_log.log("env", f"Missing Python packages: {missing}")
        raise EnvironmentError(f"Missing packages: {', '.join(missing)}")
    ui.success("Python dependencies OK")
    session_log.log("env", "Python dependencies OK")

    # 0.2 — Serial port access
    if not check_serial_access():
        ui.error("Cannot access serial ports — user not in 'uucp' group")
        ui.info("  Fix: sudo usermod -aG uucp $USER")
        ui.info("  Then reboot (logout is not sufficient for /dev permissions)")
        session_log.log("env", "Serial port access check failed")
        raise EnvironmentError("Serial port access denied")
    ui.success("Serial port access OK (uucp group)")
    session_log.log("env", "Serial port access OK")

    # 0.3 — nmcli
    if not manual_wifi:
        if not check_nmcli():
            ui.error("nmcli not found — NetworkManager required")
            ui.info("  Install: sudo pacman -S networkmanager")
            ui.info("  Or use --manual-wifi flag for manual WiFi connection")
            session_log.log("env", "nmcli not found")
            raise EnvironmentError("nmcli not available")
        ui.success("nmcli available")
        session_log.log("env", "nmcli available")

        # 0.4 — WiFi hardware
        wifi_status = check_wifi_hardware()
        if wifi_status is None:
            ui.error("No WiFi adapter found")
            session_log.log("env", "No WiFi adapter")
            raise EnvironmentError("No WiFi adapter")
        elif wifi_status == "disabled":
            ui.warn("WiFi radio is disabled")
            if ui.prompt_yn("Enable WiFi radio?", default=True):
                subprocess.run(
                    ["nmcli", "radio", "wifi", "on"],
                    capture_output=True, timeout=15,
                )
                time.sleep(2)
                ui.success("WiFi radio enabled")
                session_log.log("env", "WiFi radio enabled by user")
            else:
                raise EnvironmentError("WiFi radio disabled")
        else:
            ui.success("WiFi radio enabled")
            session_log.log("env", "WiFi radio enabled")

        # 0.5 — Record current WiFi connection
        prev_wifi = get_current_wifi_connection()
        if prev_wifi:
            ui.info(f"Current WiFi: {prev_wifi} (will restore after config)")
            session_log.log("env", f"Previous WiFi connection: {prev_wifi}")
        else:
            ui.info("No active WiFi connection to restore")
            session_log.log("env", "No previous WiFi connection")
    else:
        prev_wifi = None
        ui.info("Manual WiFi mode — skipping nmcli checks")

    # 0.6 — Internet access
    ui.info("Checking internet access...")
    internet = check_internet()
    if internet:
        ui.success("Internet available")
        session_log.log("env", "Internet available")
    else:
        ui.warn("No internet — QNH auto-fetch won't work")
        session_log.log("env", "No internet access")

    return internet, prev_wifi


def _scan_acm_devices() -> list[dict[str, str]]:
    """Scan /dev/ttyACM* for devices matching Mercury VID:PID.

    Returns list of dicts with keys: port, vid, pid, serial.
    """
    devices: list[dict[str, str]] = []
    dev_path = Path("/dev")

    for acm in sorted(dev_path.glob("ttyACM*")):
        # Read VID:PID from sysfs
        sys_path = Path(f"/sys/class/tty/{acm.name}/device")
        if not sys_path.exists():
            continue

        # Walk up to find the USB device node with idVendor/idProduct
        usb_device = sys_path.resolve()
        for _ in range(10):  # bounded walk up
            vid_file = usb_device / "idVendor"
            pid_file = usb_device / "idProduct"
            serial_file = usb_device / "serial"
            if vid_file.exists() and pid_file.exists():
                try:
                    vid = vid_file.read_text().strip().lower()
                    pid = pid_file.read_text().strip().lower()
                    serial = ""
                    if serial_file.exists():
                        serial = serial_file.read_text().strip()
                    if vid == MERCURY_VID and pid == MERCURY_PID:
                        devices.append({
                            "port": str(acm),
                            "vid": vid,
                            "pid": pid,
                            "serial": serial,
                        })
                    break
                except OSError:
                    break
            usb_device = usb_device.parent
            if usb_device == Path("/"):
                break

    return devices


def find_mercury_port() -> str | None:
    """Single-scan for a Mercury port path.

    Unlike discover_device(), this does not poll, prompt, or raise —
    just returns the first matching port or None.
    """
    devices = _scan_acm_devices()
    if devices:
        return devices[0]["port"]
    return None


def discover_device() -> dict[str, str]:
    """Phase 1: Discover a Mercury device on USB.

    Scans for ESP32-C6 USB devices, handles multiple devices, polling,
    and vanish detection.

    Returns:
        Dict with keys: port, vid, pid, serial

    Raises:
        DiscoveryError: If no device found after timeout/retries.
    """
    session_log.log("discovery", "Starting device discovery")

    devices = _scan_acm_devices()

    # 1.2 — Multiple devices
    if len(devices) > 1:
        ui.info("Multiple Mercury devices detected:")
        options: list[str] = []
        for d in devices:
            label = f"{d['port']}  (S/N: {d['serial'] or 'unknown'})"
            options.append(label)
        choice = ui.prompt_choice("Select device", options)
        idx = options.index(choice)
        device = devices[idx]
        session_log.log("discovery", f"User selected: {device['port']}")
        return device

    # 1.1 — Single device
    if len(devices) == 1:
        device = devices[0]
        ui.success(f"Mercury detected: {device['port']}")
        session_log.log("discovery", f"Device found: {device['port']} serial={device['serial']}")
        return device

    # 1.3-1.6 — No device; poll for appearance
    ui.info("No Mercury detected on USB.")
    ui.info("Plug in the Mercury via USB-C and press the power button.")

    vanish_count = 0
    deadline = time.monotonic() + DISCOVERY_TIMEOUT_S
    prev_devices: set[str] = set()

    while time.monotonic() < deadline:
        time.sleep(DISCOVERY_POLL_INTERVAL_S)

        devices = _scan_acm_devices()
        current_ports = {d["port"] for d in devices}

        # Check for vanish: device appeared in previous scan but gone now
        appeared = current_ports - prev_devices
        vanished = prev_devices - current_ports

        if vanished and not current_ports:
            # 1.6 — Device appeared then vanished
            vanish_count += 1
            session_log.log(
                "discovery",
                f"Device vanished (count={vanish_count}/{MAX_VANISH_RETRIES})",
            )
            if vanish_count >= MAX_VANISH_RETRIES:
                msg = (
                    "Device keeps disconnecting. "
                    "Try a different USB cable or port."
                )
                ui.error(msg)
                session_log.log("discovery", msg)
                raise DiscoveryError(msg)
            ui.warn(
                "Mercury connected briefly then disappeared. "
                "Press the power button ONCE (not twice) and wait for LEDs."
            )

        if len(devices) == 1:
            # 1.5 — Wait for CDC enumeration to settle
            time.sleep(DEVICE_SETTLE_MS / 1000)
            # Re-scan to confirm it's still there
            devices = _scan_acm_devices()
            if devices:
                device = devices[0]
                ui.success(f"Mercury detected: {device['port']}")
                session_log.log(
                    "discovery",
                    f"Device found after polling: {device['port']} serial={device['serial']}",
                )
                return device

        elif len(devices) > 1:
            options = []
            for d in devices:
                label = f"{d['port']}  (S/N: {d['serial'] or 'unknown'})"
                options.append(label)
            choice = ui.prompt_choice("Multiple devices — select one", options)
            idx = options.index(choice)
            device = devices[idx]
            session_log.log("discovery", f"User selected: {device['port']}")
            return device

        prev_devices = current_ports

    # 1.4 — Timeout
    msg = (
        "Not detected after 30s. "
        "Is the cable data-capable? (Charge-only cables won't work.) "
        "Try a different cable or port."
    )
    ui.error(msg)
    session_log.log("discovery", msg)
    raise DiscoveryError(msg)
