"""Mercury Config Tool — main orchestration.

Entry point for the CLI tool. Runs all 10 phases of the configuration
flow. Handles SIGINT (Ctrl+C) for graceful teardown.

This is ground tooling for flight-critical hardware.
"""

from __future__ import annotations

import argparse
import signal
import sys

from mercury_config import cdc
from mercury_config import config_engine
from mercury_config import devices
from mercury_config import discovery
from mercury_config import http_config
from mercury_config import session_log
from mercury_config import ui
from mercury_config import warnings
from mercury_config import weather
from mercury_config import wifi
from mercury_config import checkpoint


class _SessionState:
    """Mutable state for the current session, used for graceful teardown."""

    def __init__(self) -> None:
        self.serial_port = None          # serial.Serial or None
        self.mercury_ssid: str | None = None
        self.previous_wifi: str | None = None
        self.device_serial: str | None = None
        self.qnh_value: str | None = None
        self.config_pushed = False
        self.config_verified = False
        self.launch_site: str | None = None
        self.browser_opened = False
        self.second_verify_passed = False


_state = _SessionState()
_teardown_running = False


def _teardown(interrupted: bool = False) -> None:
    """Phase 10: Graceful teardown — restore WiFi, close serial, save log.

    Called both on normal exit and on SIGINT. Must never raise.
    Guarded against re-entry (SIGINT during teardown).
    """
    global _teardown_running
    if _teardown_running:
        return
    _teardown_running = True

    try:
        if interrupted:
            print()
            ui.warn("Interrupted — cleaning up...")
            session_log.log("session", "Interrupted by user (SIGINT)")

        # 10.1 — Disconnect Mercury WiFi
        if _state.mercury_ssid:
            if wifi.disconnect(_state.mercury_ssid):
                ui.info("Disconnected from Mercury WiFi")
            else:
                ui.warn(
                    "Couldn't auto-disconnect Mercury WiFi. "
                    "Reconnect to normal network manually."
                )

        # 10.2 — Restore previous WiFi
        if _state.previous_wifi:
            if wifi.restore_previous(_state.previous_wifi):
                ui.success(f"Restored WiFi: {_state.previous_wifi}")
            else:
                ui.warn(
                    f"Could not restore WiFi ({_state.previous_wifi}). "
                    "Your laptop is NOT connected to the internet. "
                    "Reconnect manually."
                )
                session_log.log("session", "WiFi restore failed — user must reconnect manually")

        # 10.3 — Close serial port
        if _state.serial_port is not None:
            try:
                _state.serial_port.close()
            except Exception:
                pass

        # 10.5 — Update managed devices list
        if _state.device_serial and _state.config_verified:
            devices.update_timestamp(_state.device_serial)

        # Print state summary on interrupt
        if interrupted:
            if _state.config_pushed and not _state.config_verified:
                ui.error(
                    "Config was pushed but NOT verified. "
                    "Check device config manually before flight."
                )
                if _state.qnh_value:
                    ui.warn(f"QNH that was being set: {_state.qnh_value} hPa")
            elif not _state.config_pushed:
                ui.info("Config was not pushed — device is unchanged.")

        # 10.4 / 10.6 ��� Close log and print path
        log_path = session_log.get_path()
        session_log.close()
        if log_path and log_path.exists():
            ui.info(f"Session log: {log_path}")

    except Exception as e:
        # Teardown must not raise
        print(f"   Teardown error: {e}", file=sys.stderr)
    finally:
        ui.teardown()


def _sigint_handler(signum: int, frame: object) -> None:
    """Handle Ctrl+C gracefully."""
    _teardown(interrupted=True)
    sys.exit(130)


def _run(args: argparse.Namespace) -> int:
    """Run the full configuration flow. Returns exit code (0=success)."""
    global _state
    _state = _SessionState()
    warnings.clear()

    ui.banner()

    # Initialise session log (serial unknown until Phase 2)
    log_path = session_log.init()
    session_log.log("session", f"Args: {vars(args)}")

    # --- Phase 0: Environment Check ---
    ui.section("ENVIRONMENT CHECK")
    try:
        internet_available, _state.previous_wifi = (
            discovery.run_environment_checks(manual_wifi=args.manual_wifi)
        )
    except discovery.EnvironmentError as e:
        ui.fatal(str(e))
        session_log.log("session", f"Environment check failed: {e}")
        return 1

    # 0.5 — Taint check: scan for incomplete sessions
    stale_sessions = checkpoint.scan_all()
    if stale_sessions:
        for stale in stale_sessions:
            ui.warn(
                f"Incomplete session for {stale['serial']} "
                f"started at {stale.get('started', 'unknown')}. "
                f"Reached: {stale.get('phase_reached', 'unknown')}."
            )
            if stale.get("config_pushed") and not stale.get("second_verify_passed"):
                ui.error(
                    f"Config was pushed but not fully verified for "
                    f"{stale['serial']}. Device may have unverified configuration."
                )
            stale_warnings = stale.get("warnings", [])
            if stale_warnings:
                ui.info(f"  Warnings from that session:")
                for w in stale_warnings:
                    ui.info(f"    [{w['category']}] {w['message']}")
        session_log.log("session", f"Found {len(stale_sessions)} stale checkpoint(s)")

    # 0.6 — Launch site selection
    ui.section("LAUNCH SITE")
    site_display = ui.prompt_choice(
        "Select launch site",
        weather.get_site_names(),
    )
    launch_site = weather.parse_site_name(site_display)
    _state.launch_site = launch_site
    session_log.log("session", f"Launch site: {launch_site}")

    # 0.7 — Pre-fetch QNH (if internet available)
    prefetched_qnh: float | None = None
    if internet_available:
        ui.info(f"Pre-fetching QNH for {launch_site}...")
        prefetched_qnh = weather.fetch_qnh(launch_site)
        if prefetched_qnh is not None:
            ui.success(f"QNH forecast ({launch_site}): {prefetched_qnh:.1f} hPa")
        else:
            ui.warn("QNH fetch failed — you'll need to enter it manually")

    # 0.8 — Load golden configs (verify they exist)
    try:
        config_engine.load_golden(2)
        config_engine.load_golden(3)
        ui.success("Golden configs loaded (Rev2 + Rev3)")
    except FileNotFoundError as e:
        ui.fatal(str(e))
        session_log.log("session", f"Golden config load failed: {e}")
        return 1

    # --- Phase 1: Device Discovery ---
    ui.section("DEVICE DISCOVERY")
    try:
        device_info = discovery.discover_device()
    except discovery.DiscoveryError as e:
        ui.fatal(str(e))
        session_log.log("session", f"Device discovery failed: {e}")
        return 1

    # --- Phase 2: CDC Identity ---
    try:
        ser, firmware, device_serial = cdc.identify_device(
            device_info["port"],
            device_info.get("serial", ""),
        )
        _state.serial_port = ser
        _state.device_serial = device_serial
    except cdc.FirmwareTooOld as e:
        ui.fatal(str(e))
        session_log.log("session", f"Firmware too old: {e}")
        return 1
    except cdc.CDCError as e:
        ui.fatal(str(e))
        session_log.log("session", f"CDC error: {e}")
        return 1

    # Rename log file with actual serial
    if device_serial:
        session_log.rename_with_serial(device_serial)

    # 2.6 — Hardware revision (from managed devices or physical inspection)
    revision: int | None = None
    if device_serial and devices.is_managed(device_serial):
        record = devices.lookup(device_serial)
        if record and "revision" in record:
            revision = record["revision"]
            sensor = "BMP390" if revision == 2 else "BMP581"
            ui.success(f"Stored revision: Rev.{revision} ({sensor})")
            session_log.log("cdc", f"Revision from managed devices: {revision}")
    if revision is None:
        revision = cdc.ask_hardware_revision()

    # 2.7 — Rev.2 warning
    if revision == 2:
        warnings.register(
            "rev2",
            "Rev.2 Mercury detected. FC6 expects 80 Hz data from Rev.3 "
            "(BMP581). Rev.2 (BMP390) outputs at 50 Hz. If using this "
            "device with FC6, you MUST review config_tunable.h \u2014 "
            "MERCURY_OUTPUT_RATE_HZ and all dependent constexprs (MPC "
            "rate, FSM timing). Rebuild and reflash FC6. Verify the "
            "config hash FC6 reports on boot matches your build.",
        )

    # 2.9 — Print device identity
    ui.device_identity(device_serial or "unknown", revision, firmware)

    # 2.10 — Check managed devices
    known_ssid: str | None = None
    if device_serial:
        if devices.is_managed(device_serial):
            record = devices.lookup(device_serial)
            ui.success("Device is in managed list")
            if record:
                known_ssid = record.get("ssid")
                ui.info(f"Known SSID: {known_ssid}")
                session_log.log("cdc", f"Known managed device, SSID={known_ssid}")
        else:
            ui.warn("This Mercury is not in the managed device list.")
            ui.info(f"Serial: {device_serial}")
            if ui.prompt_yn(
                "Adopt this device? (Sets AP password to managed password)", default=True
            ):
                cdc.adopt_device(ser)
                ui.info(
                    "Password takes effect after reboot into WiFi mode. "
                    "The device needs a power cycle."
                )
                session_log.log("cdc", f"Device adopted: {device_serial}")
            else:
                ui.warn("Proceeding without adopting — WiFi password may differ")

    # 2.10a — Create taint checkpoint
    if device_serial:
        checkpoint.write(device_serial, {
            "serial": device_serial,
            "revision": revision,
            "firmware": firmware,
            "phase_reached": "phase2_identity",
            "config_pushed": False,
            "first_verify_passed": False,
            "browser_opened": False,
            "second_verify_passed": False,
            "qnh_value": None,
            "launch_site": launch_site,
            "warnings": warnings.serialise(),
        })

    # 2.10b — Cross-device taint warning
    for stale in stale_sessions:
        if stale["serial"] != device_serial:
            warnings.register(
                "tainted_device",
                f"Unresolved session for {stale['serial']}. That device "
                f"may have unverified config.",
            )

    # Load the correct golden config
    golden = config_engine.load_golden(revision)

    # --- Phase 3: WiFi Mode & AP Discovery ---
    if not args.manual_wifi:
        try:
            # Prompt user to trigger WiFi mode
            ui.section("WIFI MODE")
            ui.info(
                "Mercury needs to be in WiFi mode. If you haven't already:"
            )
            ui.info("  1. With USB still connected, press the power button once")
            ui.info("  2. The shutdown will fail (USB holds a wakelock)")
            ui.info("  3. Mercury falls through to WiFi mode (blue LED)")
            ui.info("  4. Wait for the AP to appear (~5 seconds)")
            print()
            ui.prompt("Press Enter when ready to scan for WiFi... ")

            ssid = wifi.select_ssid(known_ssid=known_ssid)
            _state.mercury_ssid = ssid

            # 3.4 — If just adopted, needs power cycle for password
            # (The adopt flow above already warned about this)

        except wifi.WiFiError as e:
            ui.fatal(str(e))
            session_log.log("session", f"WiFi discovery failed: {e}")
            config_engine.print_golden_config_reference(golden)
            return 1

        # --- Phase 4: WiFi Connection ---
        try:
            wifi.connect(ssid)
        except wifi.WiFiError as e:
            ui.fatal(str(e))
            session_log.log("session", f"WiFi connect failed: {e}")
            config_engine.print_golden_config_reference(golden)
            return 1

        # 4.4 — Verify HTTP
        try:
            http_config.verify_http_connection()
            ui.success("Mercury web server responding")
        except http_config.HTTPConfigError as e:
            ui.fatal(str(e))
            session_log.log("session", f"HTTP verify failed: {e}")
            config_engine.print_golden_config_reference(golden)
            return 1
    else:
        ui.section("MANUAL WIFI")
        ui.info("Manual WiFi mode. Connect to the Mercury AP yourself.")
        ui.info(f"  Password: {devices.MANAGED_PASSWORD}")
        ui.info(f"  Config URL: http://192.168.0.1/settings/")
        ui.prompt("Press Enter when connected to Mercury WiFi... ")

        try:
            http_config.verify_http_connection()
            ui.success("Mercury web server responding")
        except http_config.HTTPConfigError as e:
            ui.fatal(str(e))
            return 1

    # --- Phase 5: Config Read & Parse ---
    ui.section("CONFIG READ")
    try:
        device_settings = http_config.read_settings(device_serial)
    except http_config.HTTPConfigError as e:
        ui.fatal(str(e))
        ui.error("Could not read /settings/")
        config_engine.print_golden_config_reference(golden)
        session_log.log("session", f"Settings read failed: {e}")
        return 1

    try:
        device_outputs = http_config.read_outputs(device_serial)
    except http_config.HTTPConfigError as e:
        ui.fatal(str(e))
        ui.error("Could not read /outputs/")
        config_engine.print_golden_config_reference(golden)
        session_log.log("session", f"Outputs read failed: {e}")
        return 1

    # 5.2 — Validate field count
    expected_settings_min = 20  # We expect at least this many on /settings/
    if len(device_settings) < expected_settings_min:
        ui.error(
            f"Only {len(device_settings)} fields read from /settings/ "
            f"(expected >= {expected_settings_min}). Parse may be incomplete."
        )
        config_engine.print_golden_config_reference(golden)
        return 1

    # 5.3 — Revision crossmatch check
    config_engine.check_revision_crossmatch(golden, device_settings)

    # Save SSID mapping for new devices
    if device_serial and not devices.is_managed(device_serial):
        ssid_from_device = device_settings.get("wifiname", "")
        if ssid_from_device:
            devices.adopt(
                serial=device_serial,
                ssid=ssid_from_device,
                revision=revision,
                firmware=firmware,
            )
            session_log.log(
                "devices",
                f"Saved SSID mapping: {device_serial} -> {ssid_from_device}",
            )

    # --- Phase 6: Config Verification & Diff ---
    diff = config_engine.diff_config(golden, device_settings, device_outputs)
    mismatches = config_engine.print_config_report(
        diff, device_serial, revision, firmware
    )

    # --- Phase 7: Volatile Input (QNH) ---
    current_qnh = device_settings.get("sealevel", "1013.25")
    qnh_value = config_engine.prompt_qnh(current_qnh, prefetched_qnh, launch_site)
    _state.qnh_value = qnh_value

    # --- Phase 8: Config Push & Verify ---
    if mismatches > 0:
        ui.warn(f"{mismatches} config mismatch(es) found.")
        if not ui.prompt_yn("Push corrected config?", default=True):
            ui.info("Config push cancelled by user.")
            config_engine.print_golden_config_reference(golden)
            session_log.log("session", "Config push cancelled by user")
            return 1
    else:
        ui.success("All fixed fields match golden config.")
        # Still need to push if QNH changed
        current_qnh_matches = config_engine.values_equal(current_qnh, qnh_value)
        if current_qnh_matches:
            ui.success("QNH unchanged. No config push needed.")
            _state.config_pushed = True
            _state.config_verified = True
        else:
            ui.info(f"QNH changed: {current_qnh} -> {qnh_value}")
            if not ui.prompt_yn("Push QNH update?", default=True):
                ui.info("QNH push cancelled.")
                session_log.log("session", "QNH push cancelled")
                return 0

    if not (_state.config_pushed and _state.config_verified):
        _state.config_pushed = True
        if device_serial:
            checkpoint.update(device_serial, {
                "phase_reached": "phase8_push",
                "config_pushed": True,
                "warnings": warnings.serialise(),
            })
        verified = config_engine.push_and_verify(
            golden, device_settings, device_outputs, qnh_value, device_serial
        )
        _state.config_verified = verified
        if device_serial and verified:
            checkpoint.update(device_serial, {
                "phase_reached": "phase8_verified",
                "first_verify_passed": True,
                "qnh_value": qnh_value,
                "warnings": warnings.serialise(),
            })

        if not verified:
            ui.mercury_no_go("Write verification failed")
            if ui.prompt_yn("Retry config push?", default=True):
                # Re-read and retry once
                try:
                    device_settings = http_config.read_settings(device_serial)
                    device_outputs = http_config.read_outputs(device_serial)
                    verified = config_engine.push_and_verify(
                        golden, device_settings, device_outputs,
                        qnh_value, device_serial,
                    )
                    _state.config_verified = verified
                except Exception as e:
                    ui.error(f"Retry failed: {e}")
                    session_log.log("session", f"Retry failed: {e}")

            if not _state.config_verified:
                ui.mercury_no_go("Config could not be verified. Do NOT fly.")
                session_log.log("session", "Final result: NO-GO (verification failed)")
                return 1

    # --- Phase 8.5: Optional Browser Calibration ---
    ui.section("CALIBRATION")
    if ui.prompt_yn("Do you need to calibrate in the browser?", default=False):
        import webbrowser
        warnings.register(
            "browser",
            "Browser calibration session was opened \u2014 config may have "
            "been modified outside this tool.",
        )
        ui.info("Opening http://192.168.0.1/settings/ in browser...")
        session_log.log("session", "User opened web UI for manual calibration")
        webbrowser.open("http://192.168.0.1/settings/")
        ui.prompt("Press Enter when finished with calibration... ")
        session_log.log("session", "User returned from manual calibration")
        _state.browser_opened = True
        if device_serial:
            checkpoint.update(device_serial, {
                "phase_reached": "phase8_5_browser",
                "browser_opened": True,
                "warnings": warnings.serialise(),
            })

    # --- Phase 8.6: Second Verification Pass ---
    ui.section("SECOND VERIFICATION")
    ui.info("Re-reading device config for final verification...")

    try:
        readback_settings = http_config.read_settings(device_serial)
    except http_config.HTTPConfigError as e:
        ui.fatal(f"Second verification read failed: {e}")
        session_log.log("session", f"Second verify settings read failed: {e}")
        return 1

    try:
        readback_outputs = http_config.read_outputs(device_serial)
    except http_config.HTTPConfigError as e:
        ui.fatal(f"Second verification read failed: {e}")
        session_log.log("session", f"Second verify outputs read failed: {e}")
        return 1

    second_diff = config_engine.diff_config(golden, readback_settings, readback_outputs)
    second_mismatches = config_engine.print_config_report(
        second_diff, device_serial, revision, firmware
    )

    # Check fixed fields
    if second_mismatches > 0:
        ui.mercury_no_go(
            "Second verification failed \u2014 fixed fields do not match "
            "golden config. Re-run required."
        )
        session_log.log("session", "Final result: NO-GO (second verify failed)")
        return 1

    # Check QNH matches what was entered
    readback_qnh = readback_settings.get("sealevel", "")
    if not config_engine.values_equal(readback_qnh, qnh_value):
        ui.mercury_no_go(
            f"QNH mismatch on second verification: device has "
            f"{readback_qnh}, expected {qnh_value}. Re-run required."
        )
        session_log.log(
            "session",
            f"Final result: NO-GO (QNH mismatch: {readback_qnh} vs {qnh_value})",
        )
        return 1

    ui.success("Second verification passed. All fields confirmed.")
    _state.second_verify_passed = True
    if device_serial:
        checkpoint.update(device_serial, {
            "phase_reached": "phase8_6_second_verify",
            "second_verify_passed": True,
            "warnings": warnings.serialise(),
        })

    # --- Phase 9: Flight Readiness Review ---
    ssid = _state.mercury_ssid or device_settings.get("wifiname", "unknown")
    site_display = f"{launch_site} ({weather.LAUNCH_SITES[launch_site]['label']})"

    ui.flight_readiness_summary(
        serial=device_serial or "unknown",
        revision=revision,
        ssid=ssid,
        qnh=qnh_value,
        launch_site=site_display,
    )

    # 9.2 — Warning replay
    all_warnings = warnings.get_all()
    ui.warning_replay(all_warnings)

    # 9.3 — Airframe designation
    ui.section("AIRFRAME DESIGNATION")
    stored_airframe: str | None = None
    if device_serial:
        record = devices.lookup(device_serial)
        if record:
            stored_airframe = record.get("airframe")
    if stored_airframe:
        ui.info(f"Stored: {stored_airframe}")
    ui.info("Format: C6A <rocket name> - <rocket model>")

    while True:
        airframe = ui.prompt("Airframe designation: ")
        if airframe:
            break
        ui.warn("Airframe designation is required.")

    # Save airframe to managed devices
    if device_serial:
        all_devices = devices.load()
        if device_serial in all_devices:
            all_devices[device_serial]["airframe"] = airframe
            devices.save(all_devices)
    session_log.log("session", f"Airframe: {airframe}")

    # 9.4 — Final GO
    ui.prompt_exact("Type GO to confirm flight readiness: ", "GO")

    # --- MERCURY IS GO ---
    ui.mercury_is_go(device_serial or "unknown", revision, firmware)
    ui.info(f"Airframe: {airframe}")

    session_log.log(
        "session",
        f"Final result: GO | Serial={device_serial} Rev{revision} "
        f"FW={firmware} QNH={qnh_value} Site={launch_site} "
        f"Airframe={airframe} Warnings={warnings.count()}",
    )

    # Delete taint checkpoint — successful GO
    if device_serial:
        checkpoint.delete(device_serial)

    ui.post_flight_instructions()

    return 0


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mercury-config",
        description="Mercury V1 altimeter flight configuration tool for FC6",
    )
    parser.add_argument(
        "--manual-wifi",
        action="store_true",
        help="Skip nmcli — connect to Mercury WiFi manually",
    )
    args = parser.parse_args()

    # Install SIGINT handler for graceful teardown
    signal.signal(signal.SIGINT, _sigint_handler)

    exit_code: int
    try:
        exit_code = _run(args)
    except KeyboardInterrupt:
        _teardown(interrupted=True)
        exit_code = 130
    except Exception as e:
        ui.fatal(f"Unexpected error: {e}")
        session_log.log("session", f"Unhandled exception: {e}")
        import traceback
        session_log.log("session", traceback.format_exc())
        _teardown()
        exit_code = 1
    else:
        _teardown()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
