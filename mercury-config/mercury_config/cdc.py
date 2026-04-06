"""Phase 2: CDC serial communication with Mercury.

Handles ver& command, firmware version parsing, serial number extraction,
and the device adoption flow. CDC is USB virtual serial — not UART.
"""

from __future__ import annotations

import re
import time

import termios

import serial
from serial import SerialException

from mercury_config import discovery
from mercury_config import session_log
from mercury_config import ui
from mercury_config.devices import MANAGED_PASSWORD

CDC_BAUD = 115200  # Irrelevant for USB CDC, but pyserial requires a value
CDC_READ_TIMEOUT_S = 3
CDC_FLUSH_WAIT_S = 0.2
CDC_READY_TIMEOUT_S = 30  # Total time to poll for CDC readiness
CDC_READY_POLL_S = 2.0  # Delay between readiness attempts
CDC_SEND_RETRIES = 3
CDC_RETRY_DELAY_S = 1.0


class CDCError(Exception):
    """Raised when CDC communication fails."""


class FirmwareTooOld(CDCError):
    """Raised when firmware is pre-2.30."""


def open_port(port_path: str) -> serial.Serial:
    """Open a CDC serial port to Mercury.

    Args:
        port_path: e.g. "/dev/ttyACM0"

    Returns:
        Open serial.Serial object.

    Raises:
        CDCError: On permission or busy port errors.
    """
    try:
        ser = serial.Serial(
            port=port_path,
            baudrate=CDC_BAUD,
            timeout=CDC_READ_TIMEOUT_S,
        )
        session_log.log("cdc", f"Opened {port_path}")
        return ser
    except PermissionError:
        msg = (
            f"Permission denied on {port_path}. Fix:\n"
            "  sudo usermod -aG uucp $USER\n"
            "  Then reboot."
        )
        session_log.log("cdc", f"PermissionError on {port_path}")
        raise CDCError(msg)
    except SerialException as e:
        if "busy" in str(e).lower() or "resource" in str(e).lower():
            msg = (
                f"{port_path} is busy. Close any terminal emulators "
                "(picocom, minicom, screen) that may have it open."
            )
        else:
            msg = f"Cannot open {port_path}: {e}"
        session_log.log("cdc", f"SerialException: {e}")
        raise CDCError(msg)


def send_command(ser: serial.Serial, command: str) -> str:
    """Send a command over CDC and read the response.

    Retries up to CDC_SEND_RETRIES times on serial errors (e.g. phantom
    readiness during boot) before raising.

    Args:
        ser: Open serial port.
        command: Command string (e.g. "ver&").

    Returns:
        Raw response string, stripped.

    Raises:
        CDCError: If all retries exhausted due to serial failure.
    """
    last_err: Exception | None = None
    for attempt in range(CDC_SEND_RETRIES):
        try:
            # Flush any stale data
            ser.reset_input_buffer()
            time.sleep(CDC_FLUSH_WAIT_S)
            ser.reset_input_buffer()

            session_log.log("cdc", f"TX (attempt {attempt + 1}): {command!r}")
            ser.write(command.encode("ascii"))

            # Read response (may take up to CDC_READ_TIMEOUT_S)
            response_bytes = ser.read(1024)
            response = response_bytes.decode("ascii", errors="replace").strip()
            session_log.log("cdc", f"RX: {response!r}")
            return response
        except (SerialException, OSError, termios.error) as e:
            last_err = e
            session_log.log(
                "cdc",
                f"Serial error on attempt {attempt + 1}/{CDC_SEND_RETRIES}: {e}",
            )
            if attempt < CDC_SEND_RETRIES - 1:
                time.sleep(CDC_RETRY_DELAY_S)

    raise CDCError(
        f"Serial communication failed after {CDC_SEND_RETRIES} attempts: {last_err}"
    )


def query_firmware_version(ser: serial.Serial) -> str:
    """Send ver& and parse the firmware version.

    Returns:
        Firmware version string (e.g. "2.3").

    Raises:
        FirmwareTooOld: If response doesn't contain VER: prefix.
        CDCError: If no response at all.
    """
    response = send_command(ser, "ver&")

    if not response:
        session_log.log("cdc", "No response to ver&")
        raise CDCError(
            "No response from Mercury. Is it powered on? "
            "Press the power button and wait for LEDs."
        )

    # Parse VER: prefix (FW 2.30+)
    # Response may contain echo and other data; search for VER: anywhere
    for line in response.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("VER:"):
            version = line[4:].strip()
            session_log.log("cdc", f"Firmware version: {version}")
            return version

    # No VER: prefix found — firmware is pre-2.30 (only echoed back the command)
    session_log.log("cdc", f"No VER: prefix in response — pre-2.30 firmware")
    raise FirmwareTooOld(
        "Firmware too old (pre-2.30). This tool requires FW 2.30+.\n"
        "Flash update needed: use esptool or the manufacturer's web updater."
    )


BOOT_BANNER_READ_S = 2.0  # Max time to spend reading boot banner
BOOT_BANNER_MAX_BYTES = 4096  # Cap buffer — banner is ~200 B


def try_capture_boot_serial(ser: serial.Serial) -> str | None:
    """Attempt to read the Mercury boot banner for serial/MAC.

    Mercury prints "NRB Startup..." with version/serial during cold boot.
    This only works if we catch the boot (~0.7s after power-on).

    Returns:
        Serial string if captured, None otherwise.
    """
    # Use a short read timeout so we don't overshoot the time bound
    saved_timeout = ser.timeout
    ser.timeout = 0.25

    data = b""
    start = time.monotonic()
    while time.monotonic() - start < BOOT_BANNER_READ_S:
        chunk = ser.read(256)
        if chunk:
            data += chunk
            if len(data) >= BOOT_BANNER_MAX_BYTES:
                break
        else:
            break

    ser.timeout = saved_timeout

    text = data.decode("ascii", errors="replace")
    if text:
        session_log.log_raw("boot_banner", text)

    # Look for MAC-like patterns (aa:bb:cc:dd:ee:ff)
    mac_match = re.search(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", text)
    if mac_match:
        serial_str = mac_match.group(1).lower()
        session_log.log("cdc", f"Boot banner serial: {serial_str}")
        return serial_str

    return None


def get_usb_serial(usb_serial_from_sysfs: str) -> str | None:
    """Normalise the USB serial string from sysfs into MAC format.

    ESP32-C6 USB-Serial/JTAG exposes a MAC-derived serial in the USB
    descriptor. Format varies — try to extract a MAC.

    Returns:
        MAC string (lowercase, colon-separated) or None.
    """
    if not usb_serial_from_sysfs:
        return None

    # Already colon-separated MAC
    if re.match(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$", usb_serial_from_sysfs):
        return usb_serial_from_sysfs.lower()

    # Bare hex string (12 chars)
    cleaned = usb_serial_from_sysfs.replace(":", "").replace("-", "").strip()
    if re.match(r"^[0-9a-fA-F]{12}$", cleaned):
        mac = ":".join(cleaned[i:i+2] for i in range(0, 12, 2))
        return mac.lower()

    return None


def adopt_device(ser: serial.Serial) -> None:
    """Set the AP password to the managed password via CDC.

    Sends app<password>& command. The Mercury reboots after this —
    the serial port is closed before returning. Callers must not
    reuse the serial handle.
    """
    command = f"app{MANAGED_PASSWORD}&"
    try:
        response = send_command(ser, command)
        session_log.log("cdc", f"Adopt response: {response!r}")
    except (CDCError, SerialException, OSError, termios.error) as e:
        # Device may reboot before we read the response — that's OK
        session_log.log("cdc", f"Adopt send error (expected on reboot): {e}")
    finally:
        try:
            ser.close()
        except Exception:
            pass
    ui.success("AP password set to managed password")
    ui.info("Device will reboot — serial port closed.")


def _poll_until_ready(
    port_path: str,
) -> tuple[serial.Serial, str | None, str]:
    """Poll until Mercury responds to ver& over CDC.

    Handles the full boot sequence: USB re-enumeration, phantom readiness,
    and transient I/O errors. Retries discover → open → ver& in a loop.

    Args:
        port_path: Initial port path from discovery.

    Returns:
        (open_serial_port, boot_serial_or_None, firmware_version)

    Raises:
        CDCError: If Mercury never responds within CDC_READY_TIMEOUT_S.
        FirmwareTooOld: If firmware is pre-2.30.
    """
    deadline = time.monotonic() + CDC_READY_TIMEOUT_S
    attempt = 0
    last_err: Exception | None = None

    ui.info("Waiting for Mercury to boot...")

    while time.monotonic() < deadline:
        attempt += 1

        # Re-discover port each attempt — device may re-enumerate
        refreshed = discovery.find_mercury_port()
        if not refreshed:
            session_log.log("cdc", f"Attempt {attempt}: no device found, retrying")
            time.sleep(CDC_READY_POLL_S)
            continue

        if refreshed != port_path:
            session_log.log(
                "cdc", f"Port changed: {port_path} -> {refreshed}"
            )
            port_path = refreshed

        try:
            ser = open_port(port_path)
        except CDCError as e:
            last_err = e
            session_log.log("cdc", f"Attempt {attempt}: open failed: {e}")
            time.sleep(CDC_READY_POLL_S)
            continue

        try:
            boot_serial = try_capture_boot_serial(ser)
            firmware = query_firmware_version(ser)
            return ser, boot_serial, firmware
        except FirmwareTooOld:
            # Real error, not transient — don't retry
            ser.close()
            raise
        except (CDCError, SerialException, OSError) as e:
            last_err = e
            session_log.log("cdc", f"Attempt {attempt}: CDC failed: {e}")
            ser.close()
            time.sleep(CDC_READY_POLL_S)
            continue

    raise CDCError(
        f"Mercury did not respond within {CDC_READY_TIMEOUT_S}s "
        f"({attempt} attempts). Last error: {last_err}"
    )


def identify_device(
    port_path: str,
    usb_serial: str,
) -> tuple[serial.Serial, str, str | None]:
    """Phase 2: Full CDC identity flow.

    Opens the port, queries firmware version, attempts serial extraction.

    Args:
        port_path: Serial port path.
        usb_serial: Serial string from USB sysfs descriptor (may be empty).

    Returns:
        (serial_port, firmware_version, device_serial_or_None)

    Raises:
        CDCError, FirmwareTooOld
    """
    ui.section("DEVICE IDENTIFICATION")

    ser, boot_serial, firmware = _poll_until_ready(port_path)
    ui.success(f"Firmware: {firmware}")

    # Determine serial number (MAC)
    # Priority: USB sysfs descriptor > boot banner > user input
    device_serial = get_usb_serial(usb_serial)
    if device_serial:
        session_log.log("cdc", f"Serial from USB descriptor: {device_serial}")
    elif boot_serial:
        device_serial = boot_serial
        session_log.log("cdc", f"Serial from boot banner: {device_serial}")
    else:
        ui.warn("Could not read serial number automatically.")
        raw = ui.prompt("Enter MAC from device label (e.g. b4:3a:45:99:0b:64): ")
        if raw:
            device_serial = get_usb_serial(raw) or raw.lower().strip()
        else:
            device_serial = None
            ui.warn("Proceeding without serial number — device tracking limited.")

    if device_serial:
        ui.serial_number(device_serial)
    session_log.log("cdc", f"Device serial: {device_serial or 'unknown'}")

    return ser, firmware, device_serial


def ask_hardware_revision() -> int:
    """Ask the user to identify hardware revision by physical inspection.

    Uses the GP6/GP7 pad type as the distinguishing feature — this is
    unambiguous and doesn't rely on reading a printed label.

    Returns:
        2 or 3
    """
    ui.info("Identify the hardware revision by inspecting GP6 and GP7:")
    choice = ui.prompt_choice(
        "Are GP6 and GP7 surface-mount pads or through-holes?",
        [
            "Surface-mount pads (Rev.2 \u2014 BMP390)",
            "Through-holes (Rev.3 \u2014 BMP581)",
        ],
    )
    revision = 2 if "Surface-mount" in choice else 3
    session_log.log("cdc", f"User identifies revision: {revision}")
    return revision
