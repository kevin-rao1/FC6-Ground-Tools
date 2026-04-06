"""Phase 6-8: Golden config loading, diff, push, and verify.

Loads golden configs from bundled JSON files, compares against device state,
builds read-modify-write payloads, and verifies write-back. This is the
most critical module — a bug here silently misconfigures flight hardware.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

from mercury_config import http_config
from mercury_config import session_log
from mercury_config import ui

# Golden config directory: sibling to the package
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden_configs"

QNH_MIN = 950.0
QNH_MAX = 1070.0
QNH_WARN_DELTA = 5.0


class FieldInfo(NamedTuple):
    value: str | None         # Expected value (None for volatile/identity)
    endpoint: str             # "/settings/" or "/outputs/"
    field_class: str          # "fixed", "volatile", "identity", "ignored"
    note: str


class DiffEntry(NamedTuple):
    name: str
    actual: str
    expected: str
    field_class: str
    matches: bool


class GoldenConfig:
    """Loaded golden config for a specific hardware revision."""

    def __init__(self, revision: int) -> None:
        self.revision = revision
        self.fields: dict[str, FieldInfo] = {}
        self._load(revision)

    def _load(self, revision: int) -> None:
        path = GOLDEN_DIR / f"rev{revision}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Golden config not found: {path}. Tool installation is broken."
            )

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for name, info in data["fields"].items():
            self.fields[name] = FieldInfo(
                value=info.get("value"),
                endpoint=info["endpoint"],
                field_class=info["class"],
                note=info.get("note", ""),
            )

        session_log.log("config", f"Loaded golden config rev{revision}: {len(self.fields)} fields")


def load_golden(revision: int) -> GoldenConfig:
    """Load and validate a golden config.

    Raises:
        FileNotFoundError: If golden config file is missing.
    """
    return GoldenConfig(revision)


def diff_config(
    golden: GoldenConfig,
    device_settings: dict[str, str],
    device_outputs: dict[str, str],
) -> list[DiffEntry]:
    """Compare device config against golden config.

    Args:
        golden: The golden config for this revision.
        device_settings: Fields read from /settings/.
        device_outputs: Fields read from /outputs/.

    Returns:
        List of DiffEntry for all golden config fields.
    """
    results: list[DiffEntry] = []

    for name, info in golden.fields.items():
        # Get actual value from the correct endpoint
        if info.endpoint == "/settings/":
            actual = device_settings.get(name, "")
        elif info.endpoint == "/outputs/":
            actual = device_outputs.get(name, "")
        else:
            actual = ""

        if info.field_class == "fixed":
            assert info.value is not None
            matches = values_equal(actual, info.value)
            results.append(DiffEntry(
                name=name,
                actual=actual,
                expected=info.value,
                field_class="fixed",
                matches=matches,
            ))

        elif info.field_class == "volatile":
            results.append(DiffEntry(
                name=name,
                actual=actual,
                expected="(per-launch)",
                field_class="volatile",
                matches=True,  # Volatile fields are expected to differ
            ))

        elif info.field_class == "identity":
            results.append(DiffEntry(
                name=name,
                actual=actual,
                expected="(read-only)",
                field_class="identity",
                matches=True,
            ))

    return results


def values_equal(actual: str, expected: str) -> bool:
    """Compare config values, handling decimal formatting differences.

    Mercury's form may return "15.00" vs our golden "15.00", or "500.00" vs "500".
    Uses Decimal for exact comparison — float would silently round certain
    decimal strings and could produce false matches.
    """
    if actual == expected:
        return True

    try:
        return Decimal(actual.strip()) == Decimal(expected.strip())
    except (InvalidOperation, ValueError, TypeError):
        return False


def print_config_report(
    diff: list[DiffEntry],
    serial: str | None,
    revision: int,
    firmware: str,
) -> int:
    """Print the config diff report. Returns count of mismatches."""
    ui.section("CONFIG REPORT")
    header = f"Mercury Config Report -- S/N: {serial or 'unknown'} (Rev{revision}, FW {firmware})"
    ui.info(header)
    session_log.log("config", header)
    print()

    mismatches = 0

    # Fixed fields (matching)
    matching_fixed = [d for d in diff if d.field_class == "fixed" and d.matches]
    if matching_fixed:
        for d in matching_fixed:
            ui.field_match(d.name, d.actual)
            session_log.log_config_field(d.name, d.actual, d.expected, "OK")

    # Fixed fields (mismatched) — these are the important ones
    mismatched_fixed = [d for d in diff if d.field_class == "fixed" and not d.matches]
    if mismatched_fixed:
        print()
        for d in mismatched_fixed:
            ui.field_mismatch(d.name, d.actual, d.expected)
            session_log.log_config_field(d.name, d.actual, d.expected, "MISMATCH")
            mismatches += 1

    # Volatile fields
    volatile = [d for d in diff if d.field_class == "volatile"]
    if volatile:
        print()
        for d in volatile:
            ui.field_volatile(d.name, d.actual, "set per-launch")
            session_log.log_config_field(d.name, d.actual, None, "VOLATILE")

    # Identity fields
    identity = [d for d in diff if d.field_class == "identity"]
    if identity:
        print()
        for d in identity:
            ui.field_identity(d.name, d.actual)
            session_log.log_config_field(d.name, d.actual, None, "IDENTITY")

    return mismatches


def prompt_qnh(
    current_value: str,
    prefetched_qnh: float | None,
) -> str:
    """Phase 7: Prompt user for QNH (sea-level pressure).

    Args:
        current_value: Current sealevel value from device.
        prefetched_qnh: Pre-fetched QNH from weather API, or None.

    Returns:
        The QNH value string to write.
    """
    ui.section("QNH / SEA-LEVEL PRESSURE")

    ui.info(f"Current device value: {current_value} hPa")
    if prefetched_qnh is not None:
        ui.info(f"Weather API forecast:  {prefetched_qnh:.1f} hPa")

    while True:
        raw = ui.prompt(
            f"QNH / sea-level pressure (hPa) [Enter = keep {current_value}]: "
        )

        # Empty = keep current
        if not raw:
            ui.info(f"Keeping: {current_value} hPa")
            session_log.log("config", f"QNH kept at {current_value}")
            return current_value

        # Validate
        try:
            qnh = float(raw)
        except ValueError:
            ui.warn("Enter a number (e.g. 1013.25)")
            continue

        # Range check
        if not (QNH_MIN <= qnh <= QNH_MAX):
            ui.warn(f"Outside normal atmospheric range ({QNH_MIN}-{QNH_MAX} hPa).")
            if not ui.prompt_yn("Are you sure?", default=False):
                continue

        # Compare with prefetched
        if prefetched_qnh is not None:
            delta = abs(qnh - prefetched_qnh)
            if delta > QNH_WARN_DELTA:
                ui.warn(
                    f"Your value ({qnh:.1f}) differs from forecast "
                    f"({prefetched_qnh:.1f}) by {delta:.1f} hPa."
                )
                choice = ui.prompt("Which do you want to use? [u]ser / [f]orecast: ")
                if choice.lower().startswith("f"):
                    # Validate forecast is in range (should always be, but defence in depth)
                    if QNH_MIN <= prefetched_qnh <= QNH_MAX:
                        qnh = prefetched_qnh
                        ui.info(f"Using forecast: {qnh:.1f} hPa")
                    else:
                        ui.warn(
                            f"Forecast value ({prefetched_qnh:.1f}) is outside "
                            f"valid range. Keeping your input."
                        )

        qnh_str = f"{qnh:.2f}"
        session_log.log("config", f"QNH set to {qnh_str}")
        ui.success(f"QNH: {qnh_str} hPa")
        return qnh_str


def build_write_payload(
    golden: GoldenConfig,
    device_fields: dict[str, str],
    overrides: dict[str, str],
    endpoint: str,
) -> dict[str, str]:
    """Build a complete read-modify-write payload for one endpoint.

    Starts with ALL current device values, overlays golden config values
    for fixed fields on this endpoint, and applies any explicit overrides
    (e.g. QNH). Never omits fields — see Open Question 4.

    Args:
        golden: Golden config.
        device_fields: Current values read from this endpoint.
        overrides: Explicit overrides (e.g. {"sealevel": "1013.25"}).
        endpoint: "/settings/" or "/outputs/"

    Returns:
        Complete field dict ready for write.
    """
    # Start with everything the device currently has
    payload = dict(device_fields)

    # Overlay golden config values for fixed fields on this endpoint
    for name, info in golden.fields.items():
        if info.endpoint != endpoint:
            continue
        if info.field_class == "fixed" and info.value is not None:
            payload[name] = info.value

    # Apply explicit overrides
    for name, value in overrides.items():
        if name in payload:  # Only override fields that exist on this endpoint
            payload[name] = value

    session_log.log(
        "config",
        f"Built payload for {endpoint}: {len(payload)} fields "
        f"(golden overrides + {len(overrides)} explicit overrides)",
    )
    return payload


def push_and_verify(
    golden: GoldenConfig,
    device_settings: dict[str, str],
    device_outputs: dict[str, str],
    qnh_value: str,
    serial: str | None,
) -> bool:
    """Phase 8: Push config and verify write-back.

    Builds read-modify-write payloads for both /settings/ and /outputs/,
    sends them, then reads back and compares.

    Args:
        golden: Golden config.
        device_settings: Current /settings/ values.
        device_outputs: Current /outputs/ values.
        qnh_value: QNH string to write.
        serial: Device serial for logging.

    Returns:
        True if all fields verified, False if any mismatch.
    """
    ui.section("CONFIG PUSH")

    # Build payloads
    settings_overrides = {"sealevel": qnh_value}
    settings_payload = build_write_payload(
        golden, device_settings, settings_overrides, "/settings/"
    )
    outputs_payload = build_write_payload(
        golden, device_outputs, {}, "/outputs/"
    )

    # Write /settings/
    ui.info("Writing /settings/...")
    try:
        http_config.write_settings(settings_payload)
        ui.success("Written /settings/")
    except http_config.HTTPConfigError as e:
        ui.error(f"Settings write failed: {e}")
        ui.warn(f"QNH that may need manual entry: {qnh_value} hPa")
        session_log.log("config", f"Settings write failed: {e}")
        return False

    # Write /outputs/ (only if we have golden config fields on it)
    outputs_golden_fields = [
        name for name, info in golden.fields.items()
        if info.endpoint == "/outputs/" and info.field_class == "fixed"
    ]
    if outputs_golden_fields:
        ui.info("Writing /outputs/...")
        try:
            http_config.write_outputs(outputs_payload)
            ui.success("Written /outputs/")
        except http_config.HTTPConfigError as e:
            ui.error(f"Outputs write failed: {e}")
            session_log.log("config", f"Outputs write failed: {e}")
            return False

    # Read back and verify
    ui.section("WRITE VERIFICATION")
    ui.info("Reading back config for verification...")

    try:
        readback_settings = http_config.read_settings(serial)
    except http_config.HTTPConfigError as e:
        ui.error(f"Settings read-back failed: {e}")
        session_log.log("config", f"Settings read-back failed: {e}")
        return False

    try:
        readback_outputs = http_config.read_outputs(serial)
    except http_config.HTTPConfigError as e:
        ui.error(f"Outputs read-back failed: {e}")
        session_log.log("config", f"Outputs read-back failed: {e}")
        return False

    # Compare against what we sent
    all_ok = True
    verified_count = 0

    # Verify settings
    for name, sent_value in settings_payload.items():
        if name == "sb":
            continue  # Don't verify the save token
        actual = readback_settings.get(name, "")
        if not values_equal(actual, sent_value):
            ui.field_mismatch(name, actual, sent_value)
            session_log.log("config", f"VERIFY FAIL: {name} = {actual!r} (sent: {sent_value!r})")
            all_ok = False
        else:
            verified_count += 1

    # Verify outputs (only the fields we care about)
    if outputs_golden_fields:
        for name, sent_value in outputs_payload.items():
            if name == "sb":
                continue
            actual = readback_outputs.get(name, "")
            if not values_equal(actual, sent_value):
                ui.field_mismatch(name, actual, sent_value)
                session_log.log(
                    "config",
                    f"VERIFY FAIL: {name} = {actual!r} (sent: {sent_value!r})",
                )
                all_ok = False
            else:
                verified_count += 1

    if all_ok:
        ui.success(f"Config verified. All {verified_count} fields written and confirmed.")
        session_log.log("config", f"Verification passed: {verified_count} fields OK")
    else:
        ui.error("WRITE VERIFICATION FAILED.")
        ui.error("Do NOT fly with unverified config.")
        session_log.log("config", "WRITE VERIFICATION FAILED")

    return all_ok


def print_golden_config_reference(golden: GoldenConfig) -> None:
    """Print the golden config for manual reference (used on error fallback)."""
    ui.section("GOLDEN CONFIG REFERENCE")
    ui.info("Use these values if configuring manually via browser:")
    print()
    for name, info in sorted(golden.fields.items()):
        if info.field_class == "fixed" and info.value is not None:
            note = f"  ({info.note})" if info.note else ""
            ui.info(f"  {name:25s} = {info.value}{note}")
