"""Phase 5: HTTP config read and HTML form parsing.

Reads Mercury's web-based config pages at http://192.168.0.1/settings/ and
/outputs/, parses the HTML forms to extract all field values. The HTML uses
unclosed <option> tags (valid HTML5, standard for embedded web servers).
"""

from __future__ import annotations

import datetime
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

from mercury_config import session_log
from mercury_config import ui

MERCURY_BASE_URL = "http://192.168.0.1"
HTTP_CONNECT_TIMEOUT_S = 5
HTTP_READ_TIMEOUT_S = 10
HTTP_RETRIES = 3
HTTP_RETRY_DELAY_S = 3

DEBUG_DUMP_DIR = Path.home() / ".mercury-config"


class HTTPConfigError(Exception):
    """Raised when HTTP config operations fail."""


class _MercuryFormParser(HTMLParser):
    """Parse Mercury's HTML forms, handling unclosed <option> tags.

    Extracts all <input> values and <select> selected options into a flat dict.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self._in_select: str | None = None
        self._selected_value: str | None = None
        self._first_option_value: str | None = None
        self._field_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "input":
            name = attr_dict.get("name", "")
            value = attr_dict.get("value", "")
            if name:  # Skip unnamed inputs (submit buttons)
                self.fields[name] = value
                self._field_count += 1

        elif tag == "select":
            # Start tracking a new select element
            self._in_select = attr_dict.get("name", "")
            self._selected_value = None
            self._first_option_value = None

        elif tag == "option" and self._in_select is not None:
            value = attr_dict.get("value", "")
            # Track first option as fallback
            if self._first_option_value is None:
                self._first_option_value = value
            # Check if this option is selected
            if "selected" in attr_dict:
                self._selected_value = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._in_select is not None:
            name = self._in_select
            if name:
                # Use selected value, or first option if nothing was selected
                value = self._selected_value
                if value is None:
                    value = self._first_option_value or ""
                self.fields[name] = value
                self._field_count += 1
            self._in_select = None
            self._selected_value = None
            self._first_option_value = None


def _fetch_page(endpoint: str) -> str:
    """Fetch an HTML page from Mercury with retries.

    Args:
        endpoint: e.g. "/settings/" or "/outputs/"

    Returns:
        Raw HTML string.

    Raises:
        HTTPConfigError: After all retries exhausted.
    """
    url = f"{MERCURY_BASE_URL}{endpoint}"
    last_error = ""

    for attempt in range(HTTP_RETRIES):
        try:
            session_log.log("http", f"GET {url} (attempt {attempt + 1})")
            response = requests.get(
                url,
                timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
            )
            response.raise_for_status()
            html = response.text
            session_log.log("http", f"GET {url} -> {response.status_code}, {len(html)} bytes")
            return html

        except requests.ConnectionError as e:
            last_error = f"Connection refused: {e}"
            session_log.log("http", f"GET {url} ConnectionError: {e}")
            if attempt < HTTP_RETRIES - 1:
                ui.warn(
                    "Connected to WiFi but web server not responding. "
                    "Mercury may still be booting (wait ~5s after power-on)."
                )
                time.sleep(HTTP_RETRY_DELAY_S)

        except requests.Timeout as e:
            last_error = f"Timeout: {e}"
            session_log.log("http", f"GET {url} Timeout: {e}")
            if attempt < HTTP_RETRIES - 1:
                time.sleep(HTTP_RETRY_DELAY_S)

        except requests.HTTPError as e:
            last_error = f"HTTP error: {e}"
            session_log.log("http", f"GET {url} HTTPError: {e}")
            if attempt < HTTP_RETRIES - 1:
                time.sleep(HTTP_RETRY_DELAY_S)

    raise HTTPConfigError(
        f"Failed to fetch {endpoint} after {HTTP_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


def _parse_form(html: str, endpoint: str) -> dict[str, str]:
    """Parse an HTML form page into a field→value dict.

    Uses the lenient parser first, falls back to regex extraction.

    Args:
        html: Raw HTML string.
        endpoint: For logging context.

    Returns:
        Dict of field_name → value.

    Raises:
        HTTPConfigError: If parsing produces no fields.
    """
    parser = _MercuryFormParser()
    try:
        parser.feed(html)
    except Exception as e:
        session_log.log("http", f"HTMLParser exception on {endpoint}: {e}")

    fields = parser.fields

    # If the parser found very few fields, try regex fallback
    if len(fields) < 3:
        session_log.log(
            "http",
            f"HTMLParser found only {len(fields)} fields on {endpoint}, trying regex",
        )
        fields = _regex_parse_form(html)

    session_log.log("http", f"Parsed {endpoint}: {len(fields)} fields")
    for name, value in sorted(fields.items()):
        session_log.log("http", f"  {name} = {value!r}")

    if not fields:
        raise HTTPConfigError(f"No form fields parsed from {endpoint}")

    return fields


def _regex_parse_form(html: str) -> dict[str, str]:
    """Regex-based fallback parser for when HTMLParser chokes.

    Handles the specific Mercury HTML patterns:
    - <input ... name="X" value="Y" ...>
    - <select name="X">...<option value="V" selected>...</select>
    """
    fields: dict[str, str] = {}

    # Extract input fields
    for m in re.finditer(
        r'<input[^>]*\bname="([^"]*)"[^>]*\bvalue="([^"]*)"', html
    ):
        name, value = m.group(1), m.group(2)
        if name:
            fields[name] = value

    # Also catch inputs where value comes before name
    for m in re.finditer(
        r'<input[^>]*\bvalue="([^"]*)"[^>]*\bname="([^"]*)"', html
    ):
        value, name = m.group(1), m.group(2)
        if name and name not in fields:
            fields[name] = value

    # Extract select fields
    for m in re.finditer(
        r'<select[^>]*\bname="([^"]*)"[^>]*>(.*?)</select>', html, re.DOTALL
    ):
        name = m.group(1)
        body = m.group(2)
        if not name:
            continue

        # Find selected option
        sel_match = re.search(
            r'<option\s+value="([^"]*)"[^>]*\bselected\b', body
        )
        if sel_match:
            fields[name] = sel_match.group(1)
        else:
            # No selected — use first option value
            first_match = re.search(r'<option\s+value="([^"]*)"', body)
            if first_match:
                fields[name] = first_match.group(1)

    return fields


def _dump_html_debug(
    html: str,
    endpoint: str,
    serial: str | None,
) -> Path:
    """Dump raw HTML to a debug file for later analysis."""
    DEBUG_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    serial_slug = (serial or "unknown").replace(":", "")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    endpoint_slug = endpoint.strip("/").replace("/", "_") or "root"
    filename = f"mercury_debug_{serial_slug}_{endpoint_slug}_{ts}.html"
    path = DEBUG_DUMP_DIR / filename
    path.write_text(html, encoding="utf-8")
    session_log.log("http", f"Debug HTML dumped to {path}")
    return path


def read_settings(serial: str | None = None) -> dict[str, str]:
    """Read and parse /settings/ page.

    Returns:
        Field dict from the settings page.

    Raises:
        HTTPConfigError: On fetch or parse failure.
    """
    ui.info("Reading /settings/...")
    html = ""
    try:
        html = _fetch_page("/settings/")
        session_log.log_raw("settings_html_head", html[:500])
        fields = _parse_form(html, "/settings/")
        ui.success(f"Read {len(fields)} fields from /settings/")
        return fields
    except HTTPConfigError:
        if html:
            dump_path = _dump_html_debug(html, "/settings/", serial)
            ui.error(f"Parse failed. Raw HTML dumped to: {dump_path}")
        raise


def read_outputs(serial: str | None = None) -> dict[str, str]:
    """Read and parse /outputs/ page.

    Returns:
        Field dict from the outputs page.

    Raises:
        HTTPConfigError: On fetch or parse failure.
    """
    ui.info("Reading /outputs/...")
    html = ""
    try:
        html = _fetch_page("/outputs/")
        session_log.log_raw("outputs_html_head", html[:500])
        fields = _parse_form(html, "/outputs/")
        ui.success(f"Read {len(fields)} fields from /outputs/")
        return fields
    except HTTPConfigError:
        if html:
            dump_path = _dump_html_debug(html, "/outputs/", serial)
            ui.error(f"Parse failed. Raw HTML dumped to: {dump_path}")
        raise


def write_settings(fields: dict[str, str]) -> bool:
    """Write config to /settings/ via GET with sb=y.

    Args:
        fields: Complete field dict (read-modify-write — must include ALL fields).

    Returns:
        True if the HTTP request succeeded.

    Raises:
        HTTPConfigError: After retries exhausted.
    """
    # Copy to avoid mutating caller's dict
    fields = dict(fields)
    fields["sb"] = "y"

    url = f"{MERCURY_BASE_URL}/settings/"
    session_log.log("http", f"WRITE /settings/ ({len(fields)} fields)")

    for attempt in range(2):
        try:
            # Mercury's embedded web server accepts config writes as GET
            # query parameters, not POST. This is by firmware design.
            response = requests.get(
                url,
                params=fields,
                timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
            )
            response.raise_for_status()
            session_log.log("http", f"WRITE /settings/ -> {response.status_code}")
            return True
        except Exception as e:
            session_log.log("http", f"WRITE /settings/ attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                time.sleep(2)

    raise HTTPConfigError("Config write to /settings/ failed after retries.")


def write_outputs(fields: dict[str, str]) -> bool:
    """Write config to /outputs/ via GET with sb=y.

    Args:
        fields: Complete field dict (read-modify-write — must include ALL fields).

    Returns:
        True if the HTTP request succeeded.

    Raises:
        HTTPConfigError: After retries exhausted.
    """
    fields = dict(fields)
    fields["sb"] = "y"

    url = f"{MERCURY_BASE_URL}/outputs/"
    session_log.log("http", f"WRITE /outputs/ ({len(fields)} fields)")

    for attempt in range(2):
        try:
            # GET-based write — see write_settings for explanation.
            response = requests.get(
                url,
                params=fields,
                timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
            )
            response.raise_for_status()
            session_log.log("http", f"WRITE /outputs/ -> {response.status_code}")
            return True
        except Exception as e:
            session_log.log("http", f"WRITE /outputs/ attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                time.sleep(2)

    raise HTTPConfigError("Config write to /outputs/ failed after retries.")


def verify_http_connection() -> bool:
    """Verify that Mercury's web server is responding.

    Used after WiFi connection to confirm the HTTP API is reachable.
    Retries a few times with delays (Mercury may still be booting).

    Returns:
        True if reachable.

    Raises:
        HTTPConfigError: If unreachable after retries.
    """
    for attempt in range(HTTP_RETRIES):
        try:
            response = requests.get(
                f"{MERCURY_BASE_URL}/settings/",
                timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
            )
            if response.status_code == 200:
                session_log.log("http", "HTTP connection verified")
                return True
        except Exception as e:
            session_log.log("http", f"HTTP verify attempt {attempt + 1}: {e}")
            if attempt < HTTP_RETRIES - 1:
                ui.info("Web server not responding yet, retrying...")
                time.sleep(HTTP_RETRY_DELAY_S)

    raise HTTPConfigError(
        "Connected to WiFi but web server not responding. "
        "Power cycle Mercury and restart tool."
    )
