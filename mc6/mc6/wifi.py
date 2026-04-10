"""Phase 3-4: WiFi AP connection management via nmcli.

Scans for MercuryAlt_* SSIDs, connects to the target, and provides
teardown (disconnect + restore previous connection).
"""

from __future__ import annotations

import subprocess
import time

from mc6 import session_log
from mc6 import ui
from mc6.devices import MANAGED_PASSWORD

NMCLI_TIMEOUT_S = 15
WIFI_CONNECT_RETRIES = 2
WIFI_SCAN_RETRIES = 3
WIFI_SCAN_INTERVAL_S = 5


class WiFiError(Exception):
    """Raised when WiFi operations fail."""


def scan_mercury_ssids() -> list[str]:
    """Scan for MercuryAlt_* SSIDs visible to nmcli.

    Returns sorted list of SSID strings.
    """
    try:
        # Force a fresh scan
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=NMCLI_TIMEOUT_S,
        )
        time.sleep(2)  # Allow scan results to populate

        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=NMCLI_TIMEOUT_S,
        )
        ssids: list[str] = []
        seen: set[str] = set()
        for line in result.stdout.strip().splitlines():
            ssid = line.strip()
            if ssid.startswith("MercuryAlt_") and ssid not in seen:
                ssids.append(ssid)
                seen.add(ssid)
        return sorted(ssids)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        session_log.log("wifi", f"Scan failed: {e}")
        return []


def select_ssid(
    known_ssid: str | None = None,
) -> str:
    """Phase 3: Find and select the target Mercury SSID.

    Args:
        known_ssid: SSID from managed devices list (if known).

    Returns:
        The SSID to connect to.

    Raises:
        WiFiError: If no Mercury SSIDs found after retries.
    """
    ui.section("WIFI DISCOVERY")

    for attempt in range(WIFI_SCAN_RETRIES):
        ssids = scan_mercury_ssids()
        session_log.log("wifi", f"Scan attempt {attempt + 1}: found {ssids}")

        if ssids:
            break

        if attempt < WIFI_SCAN_RETRIES - 1:
            ui.warn(
                "Mercury AP not visible. Is it in WiFi mode?\n"
                "   If the blue LED is solid, it should be broadcasting.\n"
                "   Try: power cycle (hold button until off, press once to restart) "
                "and wait 5 seconds."
            )
            if not ui.prompt_yn("Rescan?", default=True):
                raise WiFiError("User cancelled WiFi scan")
            time.sleep(WIFI_SCAN_INTERVAL_S)
    else:
        msg = (
            "No MercuryAlt_* SSIDs found after multiple scans. "
            "Ensure Mercury is powered on and in WiFi mode."
        )
        session_log.log("wifi", msg)
        raise WiFiError(msg)

    # If we know the SSID from the managed list, use it
    if known_ssid and known_ssid in ssids:
        ui.success(f"Found known device: {known_ssid}")
        session_log.log("wifi", f"Using known SSID: {known_ssid}")
        return known_ssid

    # If only one SSID visible, offer as default
    if len(ssids) == 1:
        ui.info(f"One Mercury AP visible: {ssids[0]}")
        if ui.prompt_yn(f"Connect to {ssids[0]}?", default=True):
            session_log.log("wifi", f"User accepted single SSID: {ssids[0]}")
            return ssids[0]
        raise WiFiError("User declined connection")

    # Multiple SSIDs — user must choose
    ui.info("Multiple Mercury APs visible (other teams' devices may be nearby):")
    choice = ui.prompt_choice("Select your Mercury", ssids)
    session_log.log("wifi", f"User selected SSID: {choice}")
    return choice


def connect(ssid: str) -> None:
    """Phase 4: Connect to a Mercury WiFi AP.

    Uses the managed password. Retries on failure.

    Raises:
        WiFiError: If connection fails after retries.
    """
    ui.section("WIFI CONNECTION")

    for attempt in range(WIFI_CONNECT_RETRIES + 1):
        session_log.log("wifi", f"Connecting to {ssid} (attempt {attempt + 1})")
        ui.info(f"Connecting to {ssid}...")

        try:
            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "connect",
                    ssid, "password", MANAGED_PASSWORD,
                ],
                capture_output=True, text=True, timeout=NMCLI_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            session_log.log("wifi", "nmcli connect timed out")
            if attempt < WIFI_CONNECT_RETRIES:
                ui.warn("Connection timed out. Retrying...")
                continue
            raise WiFiError(
                f"WiFi connection to {ssid} timed out. "
                "Move closer to Mercury, or check if another device "
                "is already connected."
            )

        output = result.stdout.strip() + " " + result.stderr.strip()
        session_log.log("wifi", f"nmcli output: {output}")

        if result.returncode == 0:
            ui.success(f"Connected to {ssid}")
            session_log.log("wifi", f"Connected to {ssid}")
            return

        output_lower = output.lower()
        if "password" in output_lower or "secrets" in output_lower:
            msg = (
                f"WiFi password rejected by {ssid}. "
                "This Mercury may not have the managed password set. "
                "Reconnect USB and re-run to set it."
            )
            session_log.log("wifi", msg)
            raise WiFiError(msg)

        if "not found" in output_lower or "no network" in output_lower:
            msg = f"Mercury AP '{ssid}' vanished. Check power."
            visible = scan_mercury_ssids()
            if visible:
                msg += f" Visible SSIDs: {', '.join(visible)}"
            session_log.log("wifi", msg)
            raise WiFiError(msg)

        if attempt < WIFI_CONNECT_RETRIES:
            ui.warn(f"Connection failed: {output.strip()}. Retrying...")
            time.sleep(2)
            continue

    raise WiFiError(
        f"WiFi connection to {ssid} failed after {WIFI_CONNECT_RETRIES + 1} attempts."
    )


def disconnect(ssid: str) -> bool:
    """Disconnect from Mercury WiFi.

    Returns True if successful, False otherwise. Non-critical.
    """
    try:
        result = subprocess.run(
            ["nmcli", "connection", "down", ssid],
            capture_output=True, text=True, timeout=NMCLI_TIMEOUT_S,
        )
        success = result.returncode == 0
        session_log.log("wifi", f"Disconnect {ssid}: {'OK' if success else 'failed'}")
        return success
    except (subprocess.TimeoutExpired, FileNotFoundError):
        session_log.log("wifi", f"Disconnect {ssid}: exception")
        return False


def restore_previous(connection_name: str | None) -> bool:
    """Restore the previous WiFi connection.

    Returns True if successful, False otherwise.
    """
    if not connection_name:
        return False

    try:
        result = subprocess.run(
            ["nmcli", "connection", "up", connection_name],
            capture_output=True, text=True, timeout=NMCLI_TIMEOUT_S,
        )
        success = result.returncode == 0
        session_log.log(
            "wifi",
            f"Restore {connection_name}: {'OK' if success else 'failed'}",
        )
        return success
    except (subprocess.TimeoutExpired, FileNotFoundError):
        session_log.log("wifi", f"Restore {connection_name}: exception")
        return False
