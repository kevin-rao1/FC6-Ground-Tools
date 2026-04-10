"""Terminal output formatting for Mercury config tool.

This is the ONLY module that prints to stdout. All other modules return data
or raise exceptions. Centralising output here ensures consistent formatting
and makes it trivial to audit what the user sees.
"""

from __future__ import annotations

import os
import sys

# C6 Accent: #e64097 -> RGB(230, 64, 151)
_ACCENT_R, _ACCENT_G, _ACCENT_B = 230, 64, 151

# Status colours — explicit RGB so terminal themes cannot remap them.
_GREEN_R, _GREEN_G, _GREEN_B = 52, 211, 153      # success
_AMBER_R, _AMBER_G, _AMBER_B = 251, 191, 36      # warning
_RED_R, _RED_G, _RED_B = 248, 113, 113            # error
_WHITE_R, _WHITE_G, _WHITE_B = 245, 245, 247      # C6 White — high-emphasis text

# C6 Void background — forced on startup so transparency cannot leak through.
_VOID_R, _VOID_G, _VOID_B = 10, 10, 11

_supports_truecolor: bool | None = None
_bg_forced = False


def _check_truecolor() -> bool:
    """Detect truecolor support. Conservative: default to 256-color fallback."""
    global _supports_truecolor
    if _supports_truecolor is not None:
        return _supports_truecolor
    colorterm = os.environ.get("COLORTERM", "").lower()
    _supports_truecolor = colorterm in ("truecolor", "24bit")
    return _supports_truecolor


def _force_bg() -> None:
    """Set terminal background to Void via OSC 11. No-op if not a tty."""
    global _bg_forced
    if _bg_forced or not sys.stdout.isatty():
        return
    # OSC 11 — set terminal background colour.
    # Format: rgb:RR/GG/BB with each component as a two-digit hex value.
    sys.stdout.write(
        f"\033]11;rgb:{_VOID_R:02x}/{_VOID_G:02x}/{_VOID_B:02x}\033\\"
    )
    sys.stdout.flush()
    _bg_forced = True


def _restore_bg() -> None:
    """Reset terminal background to its default via OSC 112."""
    global _bg_forced
    if not _bg_forced or not sys.stdout.isatty():
        return
    sys.stdout.write("\033]112\033\\")
    sys.stdout.flush()
    _bg_forced = False


def _sgr(code: str) -> str:
    """Wrap an SGR escape sequence. Returns empty string if not a tty."""
    if not sys.stdout.isatty():
        return ""
    return f"\033[{code}m"


def _fg(r: int, g: int, b: int) -> str:
    """24-bit foreground colour. Falls back to empty string if not a tty."""
    if not sys.stdout.isatty():
        return ""
    if _check_truecolor():
        return f"\033[38;2;{r};{g};{b}m"
    return ""


# --- Colour primitives ---

def _accent() -> str:
    if not sys.stdout.isatty():
        return ""
    if _check_truecolor():
        return f"\033[38;2;{_ACCENT_R};{_ACCENT_G};{_ACCENT_B}m"
    # Fallback: closest 256-color (168)
    return "\033[38;5;168m"


def _reset() -> str:
    return _sgr("0")


def _bold() -> str:
    return _sgr("1")


def _green() -> str:
    return _fg(_GREEN_R, _GREEN_G, _GREEN_B) or _sgr("32")


def _yellow() -> str:
    return _fg(_AMBER_R, _AMBER_G, _AMBER_B) or _sgr("33")


def _red() -> str:
    return _fg(_RED_R, _RED_G, _RED_B) or _sgr("31")


def _bold_white() -> str:
    fg = _fg(_WHITE_R, _WHITE_G, _WHITE_B)
    if fg:
        return _bold() + fg
    return _sgr("1;37")


# --- Public output functions ---

def section(title: str) -> None:
    """Print a section header: // TITLE in C6 Accent."""
    print(f"\n{_accent()}{_bold()}// {title.upper()}{_reset()}")


def info(msg: str) -> None:
    """Print informational body text."""
    print(f"   {msg}")


def success(msg: str) -> None:
    """Print success message with green checkmark."""
    print(f"   {_green()}\u2713{_reset()} {msg}")


def warn(msg: str) -> None:
    """Print warning in yellow."""
    print(f"   {_yellow()}\u26a0 {msg}{_reset()}")


def error(msg: str) -> None:
    """Print error in bold red."""
    print(f"   {_red()}{_bold()}\u2717 {msg}{_reset()}")


def fatal(msg: str) -> None:
    """Print fatal error in bold red, prefixed clearly."""
    print(f"\n   {_red()}{_bold()}FATAL: {msg}{_reset()}")


def field_match(name: str, value: str) -> None:
    """Print a config field that matches golden config."""
    print(f"   {_green()}\u2713{_reset()} {name:25s} = {value}")


def field_mismatch(name: str, actual: str, expected: str) -> None:
    """Print a config field that does NOT match golden config."""
    print(
        f"   {_red()}{_bold()}\u2717 {name:25s} = {actual:10s}"
        f"  (expected: {expected}){_reset()}"
    )


def field_volatile(name: str, value: str, note: str = "") -> None:
    """Print a volatile field (expected to differ)."""
    suffix = f"  ({note})" if note else ""
    print(f"   {_yellow()}\u26a0{_reset()} {name:25s} = {value}{suffix}")


def field_identity(name: str, value: str) -> None:
    """Print an identity field (read-only)."""
    print(f"     {name:25s} = {_bold_white()}{value}{_reset()}")


def serial_number(serial: str) -> None:
    """Print a serial number prominently."""
    print(f"   S/N: {_bold_white()}{serial}{_reset()}")


def device_identity(serial: str, revision: int, firmware: str) -> None:
    """Print full device identity block."""
    print(
        f"\n   {_bold_white()}{serial}{_reset()}"
        f"  Rev{revision}  FW {firmware}"
    )


def mercury_is_go(serial: str, revision: int, firmware: str) -> None:
    """Print the final GO message. Must be unmissable."""
    print()
    print(f"   {_accent()}{_bold()}// MERCURY IS GO{_reset()}")
    print(
        f"   {_bold_white()}{serial}{_reset()}"
        f"  Rev{revision}  FW {firmware}"
    )
    print()


def mercury_no_go(reason: str) -> None:
    """Print NO-GO with reason."""
    print()
    print(f"   {_red()}{_bold()}// MERCURY IS NO-GO{_reset()}")
    print(f"   {_red()}{reason}{_reset()}")
    print()


def prompt(msg: str) -> str:
    """Prompt user for input with C6 Accent // prefix. Returns stripped input."""
    try:
        return input(f"   {_accent()}//{_reset()} {msg}").strip()
    except EOFError:
        return ""


def prompt_yn(msg: str, default: bool = False) -> bool:
    """Yes/no prompt. Returns boolean."""
    suffix = "[Y/n]" if default else "[y/N]"
    response = prompt(f"{msg} {suffix} ")
    if not response:
        return default
    return response.lower().startswith("y")


def prompt_choice(msg: str, options: list[str]) -> str:
    """Prompt user to pick from a list of options. Returns the chosen option."""
    assert len(options) > 0, "prompt_choice called with empty options list"
    for i, opt in enumerate(options, 1):
        print(f"   [{i}] {opt}")
    _MAX_ATTEMPTS = 100
    for _ in range(_MAX_ATTEMPTS):
        raw = prompt(f"{msg} [1-{len(options)}]: ")
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        if not sys.stdin.isatty():
            raise EOFError("Non-interactive stdin cannot make a choice")
        warn(f"Enter a number between 1 and {len(options)}.")
    raise RuntimeError(f"Exceeded {_MAX_ATTEMPTS} attempts for choice prompt")


def prompt_exact(msg: str, expected: str) -> None:
    """Prompt until the user types the exact expected string.

    Case-sensitive, no shortcuts. Used for ACCEPT and GO gates.
    Raises EOFError if stdin is exhausted.
    """
    _MAX_ATTEMPTS = 100
    for _ in range(_MAX_ATTEMPTS):
        response = prompt(msg)
        if response == expected:
            return
        if not sys.stdin.isatty():
            raise EOFError(f"Non-interactive stdin cannot provide '{expected}'")
        warn(f"Type {expected} exactly to proceed.")
    raise RuntimeError(f"Exceeded {_MAX_ATTEMPTS} attempts for exact prompt")


def flight_readiness_summary(
    serial: str,
    revision: int,
    ssid: str,
    qnh: str,
    launch_site: str,
) -> None:
    """Print the Phase 9 summary block."""
    section("FLIGHT READINESS REVIEW")
    info(f"Serial:    {_bold_white()}{serial}{_reset()}")
    info(f"Revision:  Rev.{revision} ({'BMP390' if revision == 2 else 'BMP581'})")
    info(f"SSID:      {ssid}")
    info(f"QNH:       {qnh} hPa")
    info(f"Site:      {launch_site}")
    print()


def warning_replay(warnings_list: list[tuple[str, str]]) -> None:
    """Replay all flight-safety warnings, demanding ACCEPT for each.

    Args:
        warnings_list: List of (category, message) tuples.
    """
    if not warnings_list:
        info("No warnings to review.")
        return

    total = len(warnings_list)
    for i, (_category, message) in enumerate(warnings_list, 1):
        print()
        print(f"   {_yellow()}\u26a0 [{i}/{total}] {message}{_reset()}")
        prompt_exact("Type ACCEPT to acknowledge: ", "ACCEPT")
        success(f"Warning {i}/{total} acknowledged.")


def banner() -> None:
    """Print startup banner. Forces terminal background to C6 Void."""
    _force_bg()
    print(f"\n{_accent()}{_bold()}// MC6{_reset()}  v0.1.0")
    print(f"   FC6 Ground Tools — flight configuration for Mercury V1")
    print()


def post_flight_instructions() -> None:
    """Print post-config instructions for flight prep."""
    section("FLIGHT PREP")
    info("To arm for flight:")
    info("  1. Disconnect USB")
    info("  2. Install Mercury in rocket (USB port down)")
    info("  3. Press power button once")
    info("  4. Wait for green flashing LED (~8 seconds)")
    info("  5. Remember to check we're showing the geen light to UKROCism before launch")  # "geen" and "UKROCism" are deliberate
    print()


def teardown() -> None:
    """Restore terminal to its default state. Call on every exit path."""
    if sys.stdout.isatty():
        # SGR 0 — clear any leftover foreground colour / bold
        sys.stdout.write("\033[0m")
        sys.stdout.flush()
    _restore_bg()
