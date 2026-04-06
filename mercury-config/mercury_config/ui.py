"""Terminal output formatting for Mercury config tool.

This is the ONLY module that prints to stdout. All other modules return data
or raise exceptions. Centralising output here ensures consistent formatting
and makes it trivial to audit what the user sees.
"""

from __future__ import annotations

import os
import sys

# Signal Pink: #e64097 -> RGB(230, 64, 151)
_PINK_R, _PINK_G, _PINK_B = 230, 64, 151

_supports_truecolor: bool | None = None


def _check_truecolor() -> bool:
    """Detect truecolor support. Conservative: default to 256-color fallback."""
    global _supports_truecolor
    if _supports_truecolor is not None:
        return _supports_truecolor
    colorterm = os.environ.get("COLORTERM", "").lower()
    _supports_truecolor = colorterm in ("truecolor", "24bit")
    return _supports_truecolor


def _sgr(code: str) -> str:
    """Wrap an SGR escape sequence. Returns empty string if not a tty."""
    if not sys.stdout.isatty():
        return ""
    return f"\033[{code}m"


# --- Colour primitives ---

def _pink() -> str:
    if not sys.stdout.isatty():
        return ""
    if _check_truecolor():
        return f"\033[38;2;{_PINK_R};{_PINK_G};{_PINK_B}m"
    # Fallback: closest 256-color (168 is a reasonable pink)
    return "\033[38;5;168m"


def _reset() -> str:
    return _sgr("0")


def _bold() -> str:
    return _sgr("1")


def _green() -> str:
    return _sgr("32")


def _yellow() -> str:
    return _sgr("33")


def _red() -> str:
    return _sgr("31")


def _bold_white() -> str:
    return _sgr("1;37")


# --- Public output functions ---

def section(title: str) -> None:
    """Print a section header: // TITLE in Signal Pink."""
    print(f"\n{_pink()}{_bold()}// {title.upper()}{_reset()}")


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
    print(f"   {_pink()}{_bold()}// MERCURY IS GO{_reset()}")
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
    """Prompt user for input with Signal Pink // prefix. Returns stripped input."""
    try:
        return input(f"   {_pink()}//{_reset()} {msg}").strip()
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
    for i, opt in enumerate(options, 1):
        print(f"   [{i}] {opt}")
    while True:
        raw = prompt(f"{msg} [1-{len(options)}]: ")
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        warn(f"Enter a number between 1 and {len(options)}.")


def banner() -> None:
    """Print startup banner."""
    print(f"\n{_pink()}{_bold()}// MERCURY CONFIG TOOL{_reset()}  v0.1.0")
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
    info("  5. Remember to check we're showing the green light to UKROCism before launch")
    print()
