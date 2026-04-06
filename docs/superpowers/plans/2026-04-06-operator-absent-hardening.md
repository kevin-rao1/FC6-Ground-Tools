# Operator-Absent Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Mercury Config Tool so that every off-nominal condition is explicitly acknowledged, no config goes unverified, and a crashed session cannot silently leave a device in an unknown state.

**Architecture:** Six changes layered onto the existing 10-phase flow. Two new modules (`warnings.py`, `checkpoint.py`) provide infrastructure; the remaining changes modify existing modules. The flow is restructured so GO moves after all verification and a mandatory review gate.

**Tech Stack:** Python 3.11+, pytest, pathlib, JSON persistence. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-06-operator-absent-hardening-design.md`

**Test runner:** `cd mercury-config && python -m pytest -v`

---

### File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `mercury_config/warnings.py` | **New** | Flight-safety warning registry: register, accumulate, serialise, replay |
| `mercury_config/checkpoint.py` | **New** | Taint checkpoint: write/read/delete/scan `~/.mercury-config/sessions/` |
| `mercury_config/weather.py` | Modify | Launch site coordinates, `fetch_qnh()` takes site parameter |
| `mercury_config/devices.py` | Modify | Add `airframe` field to `DeviceRecord` |
| `mercury_config/cdc.py` | Modify | Rewrite `ask_hardware_revision()` prompt to GP6/GP7 question |
| `mercury_config/config_engine.py` | Modify | Harden `prompt_qnh()`, add `check_revision_crossmatch()` |
| `mercury_config/ui.py` | Modify | Add `prompt_exact()`, `flight_readiness_summary()` |
| `mercury_config/main.py` | Modify | Full flow restructure: taint check, launch site, Phase 8.5/8.6, Phase 9 review |
| `tests/test_warnings.py` | **New** | Warning registry tests |
| `tests/test_checkpoint.py` | **New** | Taint checkpoint tests |
| `tests/test_config_engine.py` | Modify | QNH hardening tests, revision cross-check tests |
| `tests/test_weather.py` | **New** | Launch site coordinate selection tests |

---

### Task 1: Warning Registry (`warnings.py`)

**Files:**
- Create: `mercury-config/mercury_config/warnings.py`
- Test: `mercury-config/tests/test_warnings.py`

This is the foundation — most subsequent tasks depend on it.

- [ ] **Step 1: Write test file with all warning registry tests**

```python
"""Tests for the flight-safety warning registry.

The warning registry is the backbone of the GO gate — every off-nominal
condition passes through it. A bug here means warnings silently vanish.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercury_config import warnings


class TestRegister:
    """register() must print, log, and store."""

    def setup_method(self) -> None:
        warnings.clear()

    def test_register_stores_warning(self) -> None:
        with patch("mercury_config.warnings.ui"), \
             patch("mercury_config.warnings.session_log"):
            warnings.register("test_cat", "test message")
        assert warnings.count() == 1
        stored = warnings.get_all()
        assert stored[0] == ("test_cat", "test message")

    def test_register_calls_ui_warn(self) -> None:
        with patch("mercury_config.warnings.ui") as mock_ui, \
             patch("mercury_config.warnings.session_log"):
            warnings.register("cat", "something bad")
        mock_ui.warn.assert_called_once_with("something bad")

    def test_register_calls_session_log(self) -> None:
        with patch("mercury_config.warnings.ui"), \
             patch("mercury_config.warnings.session_log") as mock_log:
            warnings.register("cat", "something bad")
        mock_log.log.assert_called_once_with("warning", "[cat] something bad")

    def test_multiple_warnings_accumulate(self) -> None:
        with patch("mercury_config.warnings.ui"), \
             patch("mercury_config.warnings.session_log"):
            warnings.register("a", "first")
            warnings.register("b", "second")
            warnings.register("c", "third")
        assert warnings.count() == 3
        all_warnings = warnings.get_all()
        assert all_warnings[0] == ("a", "first")
        assert all_warnings[1] == ("b", "second")
        assert all_warnings[2] == ("c", "third")


class TestClear:
    def test_clear_empties_registry(self) -> None:
        with patch("mercury_config.warnings.ui"), \
             patch("mercury_config.warnings.session_log"):
            warnings.register("a", "first")
        assert warnings.count() == 1
        warnings.clear()
        assert warnings.count() == 0
        assert warnings.get_all() == []


class TestSerialise:
    """Round-trip through serialise/deserialise must be lossless."""

    def setup_method(self) -> None:
        warnings.clear()

    def test_round_trip(self) -> None:
        with patch("mercury_config.warnings.ui"), \
             patch("mercury_config.warnings.session_log"):
            warnings.register("rev2", "Rev.2 detected")
            warnings.register("qnh_delta", "QNH differs by 7 hPa")

        data = warnings.serialise()
        assert len(data) == 2
        assert data[0] == {"category": "rev2", "message": "Rev.2 detected"}
        assert data[1] == {"category": "qnh_delta", "message": "QNH differs by 7 hPa"}

        # Deserialise into a fresh state
        warnings.clear()
        warnings.deserialise(data)
        assert warnings.count() == 2
        assert warnings.get_all()[0] == ("rev2", "Rev.2 detected")

    def test_serialise_empty(self) -> None:
        assert warnings.serialise() == []

    def test_deserialise_empty(self) -> None:
        warnings.deserialise([])
        assert warnings.count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mercury-config && python -m pytest tests/test_warnings.py -v`
Expected: `ModuleNotFoundError: No module named 'mercury_config.warnings'`

- [ ] **Step 3: Implement `warnings.py`**

```python
"""Flight-safety warning registry.

Every off-nominal condition that must be reviewed before GO passes through
register(). The Phase 9 review gate calls get_all() to replay each warning
and demand ACCEPT.

All flight-safety warnings are findable with: grep warnings.register
"""

from __future__ import annotations

from mercury_config import session_log
from mercury_config import ui

_warnings: list[tuple[str, str]] = []


def register(category: str, message: str) -> None:
    """Register a flight-safety warning.

    Immediately prints via ui.warn() and logs to session log.
    Stored for replay at Phase 9 Flight Readiness Review.
    """
    ui.warn(message)
    session_log.log("warning", f"[{category}] {message}")
    _warnings.append((category, message))


def get_all() -> list[tuple[str, str]]:
    """Return all registered warnings as (category, message) tuples."""
    return list(_warnings)


def count() -> int:
    """Return the number of registered warnings."""
    return len(_warnings)


def clear() -> None:
    """Clear all warnings. Used on session reset."""
    _warnings.clear()


def serialise() -> list[dict[str, str]]:
    """Serialise warnings for taint checkpoint persistence."""
    return [
        {"category": cat, "message": msg}
        for cat, msg in _warnings
    ]


def deserialise(data: list[dict[str, str]]) -> None:
    """Load warnings from checkpoint data. Does NOT print or log —
    these are historical warnings from a previous crashed session."""
    for entry in data:
        _warnings.append((entry["category"], entry["message"]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mercury-config && python -m pytest tests/test_warnings.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mercury-config/mercury_config/warnings.py mercury-config/tests/test_warnings.py
git commit -m "feat: add flight-safety warning registry (warnings.py)

New module accumulates warnings throughout a session and replays them
at the Phase 9 Flight Readiness Review gate. Every off-nominal condition
must be explicitly ACCEPTed before GO."
```

---

### Task 2: Taint Checkpoint (`checkpoint.py`)

**Files:**
- Create: `mercury-config/mercury_config/checkpoint.py`
- Test: `mercury-config/tests/test_checkpoint.py`

Depends on: Task 1 (imports `warnings.serialise()`)

- [ ] **Step 1: Write test file**

```python
"""Tests for taint checkpoint persistence.

The checkpoint is the safety net for crashed sessions. If write/read/delete
is broken, a crashed session leaves no trace and the operator has no idea
the device may be in a partially-configured state.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercury_config import checkpoint


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Override the sessions directory to a temp dir."""
    d = tmp_path / "sessions"
    d.mkdir()
    checkpoint._SESSIONS_DIR = d
    yield d
    checkpoint._SESSIONS_DIR = checkpoint._DEFAULT_SESSIONS_DIR


class TestWriteAndRead:
    def test_write_creates_file(self, sessions_dir: Path) -> None:
        checkpoint.write("aa:bb:cc:dd:ee:ff", {
            "serial": "aa:bb:cc:dd:ee:ff",
            "revision": 3,
            "firmware": "2.30",
            "phase_reached": "phase2_identity",
            "config_pushed": False,
            "first_verify_passed": False,
            "browser_opened": False,
            "second_verify_passed": False,
            "qnh_value": None,
            "launch_site": "Cox's Field",
            "warnings": [],
        })
        path = sessions_dir / "aa:bb:cc:dd:ee:ff.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["serial"] == "aa:bb:cc:dd:ee:ff"
        assert data["revision"] == 3
        assert "started" in data

    def test_read_returns_data(self, sessions_dir: Path) -> None:
        checkpoint.write("aa:bb:cc:dd:ee:ff", {
            "serial": "aa:bb:cc:dd:ee:ff",
            "revision": 3,
            "firmware": "2.30",
            "phase_reached": "phase2_identity",
            "config_pushed": False,
            "first_verify_passed": False,
            "browser_opened": False,
            "second_verify_passed": False,
            "qnh_value": None,
            "launch_site": "Cox's Field",
            "warnings": [],
        })
        data = checkpoint.read("aa:bb:cc:dd:ee:ff")
        assert data is not None
        assert data["serial"] == "aa:bb:cc:dd:ee:ff"

    def test_read_nonexistent_returns_none(self, sessions_dir: Path) -> None:
        assert checkpoint.read("no:su:ch:de:vi:ce") is None


class TestDelete:
    def test_delete_removes_file(self, sessions_dir: Path) -> None:
        checkpoint.write("aa:bb:cc:dd:ee:ff", {
            "serial": "aa:bb:cc:dd:ee:ff",
            "revision": 3,
            "firmware": "2.30",
            "phase_reached": "phase9_go",
            "config_pushed": True,
            "first_verify_passed": True,
            "browser_opened": False,
            "second_verify_passed": True,
            "qnh_value": "1013.25",
            "launch_site": "Cox's Field",
            "warnings": [],
        })
        checkpoint.delete("aa:bb:cc:dd:ee:ff")
        assert checkpoint.read("aa:bb:cc:dd:ee:ff") is None

    def test_delete_nonexistent_is_noop(self, sessions_dir: Path) -> None:
        checkpoint.delete("no:su:ch:de:vi:ce")  # Should not raise


class TestScanAll:
    def test_scan_finds_all_checkpoints(self, sessions_dir: Path) -> None:
        for serial in ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]:
            checkpoint.write(serial, {
                "serial": serial,
                "revision": 3,
                "firmware": "2.30",
                "phase_reached": "phase8_push",
                "config_pushed": True,
                "first_verify_passed": False,
                "browser_opened": False,
                "second_verify_passed": False,
                "qnh_value": "1013.25",
                "launch_site": "Cox's Field",
                "warnings": [],
            })
        results = checkpoint.scan_all()
        assert len(results) == 2
        serials = {r["serial"] for r in results}
        assert "aa:bb:cc:dd:ee:01" in serials
        assert "aa:bb:cc:dd:ee:02" in serials

    def test_scan_empty_dir(self, sessions_dir: Path) -> None:
        assert checkpoint.scan_all() == []


class TestUpdate:
    def test_update_preserves_started_timestamp(self, sessions_dir: Path) -> None:
        checkpoint.write("aa:bb:cc:dd:ee:ff", {
            "serial": "aa:bb:cc:dd:ee:ff",
            "revision": 3,
            "firmware": "2.30",
            "phase_reached": "phase2_identity",
            "config_pushed": False,
            "first_verify_passed": False,
            "browser_opened": False,
            "second_verify_passed": False,
            "qnh_value": None,
            "launch_site": "Cox's Field",
            "warnings": [],
        })
        original = checkpoint.read("aa:bb:cc:dd:ee:ff")
        assert original is not None
        original_started = original["started"]

        checkpoint.update("aa:bb:cc:dd:ee:ff", {
            "phase_reached": "phase8_push",
            "config_pushed": True,
        })
        updated = checkpoint.read("aa:bb:cc:dd:ee:ff")
        assert updated is not None
        assert updated["phase_reached"] == "phase8_push"
        assert updated["config_pushed"] is True
        assert updated["started"] == original_started
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mercury-config && python -m pytest tests/test_checkpoint.py -v`
Expected: `ModuleNotFoundError: No module named 'mercury_config.checkpoint'`

- [ ] **Step 3: Implement `checkpoint.py`**

```python
"""Taint checkpoint persistence for crash recovery.

Writes session state to ~/.mercury-config/sessions/<serial>.json at each
phase boundary. Deleted only on successful GO. A checkpoint left behind
after a crash forces the operator to acknowledge the incomplete session
and re-run from scratch.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from mercury_config import session_log

_DEFAULT_SESSIONS_DIR = Path.home() / ".mercury-config" / "sessions"
_SESSIONS_DIR = _DEFAULT_SESSIONS_DIR


def write(serial: str, data: dict[str, Any]) -> None:
    """Write or overwrite a taint checkpoint for a device.

    Adds a 'started' timestamp if not already present.
    """
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    if "started" not in data:
        data["started"] = datetime.datetime.now().isoformat(timespec="seconds")

    path = _SESSIONS_DIR / f"{serial}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    session_log.log("checkpoint", f"Wrote checkpoint for {serial}: {data['phase_reached']}")


def read(serial: str) -> dict[str, Any] | None:
    """Read a taint checkpoint. Returns None if no checkpoint exists."""
    path = _SESSIONS_DIR / f"{serial}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def update(serial: str, updates: dict[str, Any]) -> None:
    """Update specific fields in an existing checkpoint.

    Preserves all existing fields (including 'started' timestamp).
    No-op if checkpoint doesn't exist.
    """
    data = read(serial)
    if data is None:
        return
    data.update(updates)

    path = _SESSIONS_DIR / f"{serial}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    session_log.log("checkpoint", f"Updated checkpoint for {serial}: {list(updates.keys())}")


def delete(serial: str) -> None:
    """Delete a taint checkpoint. Called only on successful GO."""
    path = _SESSIONS_DIR / f"{serial}.json"
    if path.exists():
        path.unlink()
        session_log.log("checkpoint", f"Deleted checkpoint for {serial} (GO)")


def scan_all() -> list[dict[str, Any]]:
    """Scan for all existing taint checkpoints.

    Returns list of checkpoint data dicts. Used at startup to detect
    incomplete sessions from previous runs.
    """
    if not _SESSIONS_DIR.exists():
        return []

    results: list[dict[str, Any]] = []
    for path in _SESSIONS_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mercury-config && python -m pytest tests/test_checkpoint.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mercury-config/mercury_config/checkpoint.py mercury-config/tests/test_checkpoint.py
git commit -m "feat: add taint checkpoint for crash recovery (checkpoint.py)

Persists session state to disk at phase boundaries. Deleted only on
successful GO. Stale checkpoints from crashed sessions force a full
re-run and warn the operator about potentially dirty device state."
```

---

### Task 3: Launch Site Selection & Weather Hardening (`weather.py`)

**Files:**
- Modify: `mercury-config/mercury_config/weather.py`
- Test: `mercury-config/tests/test_weather.py`

- [ ] **Step 1: Write weather tests**

```python
"""Tests for launch site selection and site-specific QNH fetch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercury_config.weather import LAUNCH_SITES, get_site_names, get_site_coords


class TestLaunchSites:
    def test_three_sites_defined(self) -> None:
        assert len(LAUNCH_SITES) == 3

    def test_coxs_field_coords(self) -> None:
        lat, lon = get_site_coords("Cox's Field")
        assert abs(lat - 51.6695) < 0.001
        assert abs(lon - (-1.3680)) < 0.001

    def test_chippenham_coords(self) -> None:
        lat, lon = get_site_coords("Chippenham")
        assert abs(lat - 51.4592) < 0.001
        assert abs(lon - (-2.1306)) < 0.001

    def test_farnborough_coords(self) -> None:
        lat, lon = get_site_coords("Farnborough")
        assert abs(lat - 51.2803) < 0.001
        assert abs(lon - (-0.7779)) < 0.001

    def test_get_site_names_returns_display_strings(self) -> None:
        names = get_site_names()
        assert len(names) == 3
        assert "Cox's Field (C6 Aerospace)" in names
        assert "Chippenham (UKROC Regional)" in names
        assert "Farnborough (Internationals)" in names

    def test_unknown_site_raises(self) -> None:
        with pytest.raises(KeyError):
            get_site_coords("Narnia")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mercury-config && python -m pytest tests/test_weather.py -v`
Expected: FAIL — `get_site_names` and `get_site_coords` don't exist yet

- [ ] **Step 3: Rewrite `weather.py`**

Replace the entire contents of `mercury-config/mercury_config/weather.py` with:

```python
"""Best-effort QNH pre-fetch from Open-Meteo weather API.

Uses Open-Meteo (no API key required, free tier). Falls back gracefully —
this is a convenience, not a dependency. The user always confirms or
overrides the value.
"""

from __future__ import annotations

from mercury_config import session_log

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
FETCH_TIMEOUT_S = 5

LAUNCH_SITES: dict[str, dict[str, float | str]] = {
    "Cox's Field": {
        "lat": 51.6695,
        "lon": -1.3680,
        "label": "C6 Aerospace",
    },
    "Chippenham": {
        "lat": 51.4592,
        "lon": -2.1306,
        "label": "UKROC Regional",
    },
    "Farnborough": {
        "lat": 51.2803,
        "lon": -0.7779,
        "label": "Internationals",
    },
}


def get_site_names() -> list[str]:
    """Return display strings for ui.prompt_choice().

    Format: "Site Name (Label)" e.g. "Cox's Field (C6 Aerospace)"
    """
    return [
        f"{name} ({site['label']})"
        for name, site in LAUNCH_SITES.items()
    ]


def get_site_coords(site_name: str) -> tuple[float, float]:
    """Return (lat, lon) for a site name.

    Raises KeyError if site_name is not in LAUNCH_SITES.
    """
    site = LAUNCH_SITES[site_name]
    return float(site["lat"]), float(site["lon"])


def parse_site_name(display_string: str) -> str:
    """Extract the site name from a display string.

    "Cox's Field (C6 Aerospace)" -> "Cox's Field"
    """
    return display_string.split(" (")[0]


def fetch_qnh(site_name: str) -> float | None:
    """Fetch current sea-level pressure (QNH) from Open-Meteo for a site.

    Args:
        site_name: Key into LAUNCH_SITES (e.g. "Cox's Field").

    Returns:
        Pressure in hPa, or None on any failure.
    """
    try:
        import requests

        lat, lon = get_site_coords(site_name)

        params = {
            "latitude": str(lat),
            "longitude": str(lon),
            "current": "pressure_msl",
            "forecast_days": "1",
        }

        session_log.log("weather", f"Fetching QNH from Open-Meteo for {site_name}...")
        response = requests.get(
            OPEN_METEO_URL,
            params=params,
            timeout=FETCH_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()

        pressure = data.get("current", {}).get("pressure_msl")
        if pressure is not None:
            qnh = float(pressure)
            session_log.log("weather", f"QNH from API ({site_name}): {qnh} hPa")
            return qnh

        session_log.log("weather", f"No pressure_msl in response: {data}")
        return None

    except Exception as e:
        session_log.log("weather", f"QNH fetch failed: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mercury-config && python -m pytest tests/test_weather.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run full test suite to check nothing broke**

Run: `cd mercury-config && python -m pytest -v`
Expected: All existing tests still pass (weather module is only called from `main.py`, which has no tests)

- [ ] **Step 6: Commit**

```bash
git add mercury-config/mercury_config/weather.py mercury-config/tests/test_weather.py
git commit -m "feat: add launch site selection to weather module

Replace hardcoded central-UK coordinates with three launch sites:
Cox's Field, Chippenham, Farnborough. fetch_qnh() now takes a site
name parameter for site-specific forecasts."
```

---

### Task 4: Device Record — Add Airframe Field (`devices.py`)

**Files:**
- Modify: `mercury-config/mercury_config/devices.py`

Minimal change — just add the optional field to the TypedDict.

- [ ] **Step 1: Update `DeviceRecord` TypedDict**

In `mercury-config/mercury_config/devices.py`, change the `DeviceRecord` class (lines 20-24) to:

```python
class DeviceRecord(TypedDict, total=False):
    ssid: str
    revision: int
    firmware: str
    last_configured: str
    airframe: str
```

Note: `total=False` makes all fields optional, which is needed because existing `devices.json` files won't have `airframe`. The existing code already uses `.get()` for lookups, so this is backwards-compatible.

- [ ] **Step 2: Run full test suite**

Run: `cd mercury-config && python -m pytest -v`
Expected: All tests pass (TypedDict change is structural, not behavioural)

- [ ] **Step 3: Commit**

```bash
git add mercury-config/mercury_config/devices.py
git commit -m "feat: add airframe field to DeviceRecord

Optional field for tracking which rocket a Mercury is assigned to.
Backwards-compatible with existing devices.json files."
```

---

### Task 5: Hardware Revision Prompt Rewrite (`cdc.py`)

**Files:**
- Modify: `mercury-config/mercury_config/cdc.py` (lines 256-271)

- [ ] **Step 1: Rewrite `ask_hardware_revision()`**

Replace lines 256-271 in `mercury-config/mercury_config/cdc.py` with:

```python
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
```

- [ ] **Step 2: Run full test suite**

Run: `cd mercury-config && python -m pytest -v`
Expected: All tests pass (this function is only called interactively from `main.py`)

- [ ] **Step 3: Commit**

```bash
git add mercury-config/mercury_config/cdc.py
git commit -m "feat: rewrite revision prompt to use GP6/GP7 pad inspection

Replace 'type 2 or 3' prompt with unambiguous physical inspection
question about pad type. Surface-mount = Rev.2, through-holes = Rev.3."
```

---

### Task 6: QNH Hardening (`config_engine.py`)

**Files:**
- Modify: `mercury-config/mercury_config/config_engine.py` (lines 204-270)
- Modify: `mercury-config/tests/test_config_engine.py`

- [ ] **Step 1: Write QNH hardening tests**

Add to the end of `mercury-config/tests/test_config_engine.py`:

```python
from unittest.mock import patch, call


class TestPromptQnhHardened:
    """QNH prompt must reject empty input and require explicit numeric entry."""

    @patch("mercury_config.config_engine.ui")
    def test_rejects_empty_input(self, mock_ui) -> None:
        """Enter-to-keep must NOT be accepted."""
        # First call returns empty, second returns valid number
        mock_ui.prompt.side_effect = ["", "1013.25"]
        mock_ui.warn = lambda msg: None
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mercury_config.config_engine.session_log"):
            result = config_engine.prompt_qnh(
                current_value="1010.00",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1013.25"
        # Verify we were prompted twice (first empty was rejected)
        assert mock_ui.prompt.call_count == 2

    @patch("mercury_config.config_engine.ui")
    def test_accepts_valid_numeric(self, mock_ui) -> None:
        mock_ui.prompt.return_value = "1025.50"
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mercury_config.config_engine.session_log"), \
             patch("mercury_config.config_engine.warnings"):
            result = config_engine.prompt_qnh(
                current_value="1013.25",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1025.50"

    @patch("mercury_config.config_engine.ui")
    def test_rejects_non_numeric(self, mock_ui) -> None:
        mock_ui.prompt.side_effect = ["abc", "1013.25"]
        mock_ui.warn = lambda msg: None
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mercury_config.config_engine.session_log"), \
             patch("mercury_config.config_engine.warnings"):
            result = config_engine.prompt_qnh(
                current_value="1010.00",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1013.25"
        assert mock_ui.prompt.call_count == 2
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd mercury-config && python -m pytest tests/test_config_engine.py::TestPromptQnhHardened -v`
Expected: FAIL — `prompt_qnh()` doesn't accept `launch_site` parameter yet

- [ ] **Step 3: Rewrite `prompt_qnh()` in `config_engine.py`**

Replace lines 204-270 in `mercury-config/mercury_config/config_engine.py` with:

```python
def prompt_qnh(
    current_value: str,
    prefetched_qnh: float | None,
    launch_site: str,
) -> str:
    """Phase 7: Prompt user for QNH (sea-level pressure).

    No empty input accepted. Operator must type a numeric value every time.

    Args:
        current_value: Current sealevel value from device.
        prefetched_qnh: Pre-fetched QNH from weather API, or None.
        launch_site: Name of selected launch site for display.

    Returns:
        The QNH value string to write.
    """
    ui.section("QNH / SEA-LEVEL PRESSURE")

    ui.info(f"Current device value: {current_value} hPa")
    if prefetched_qnh is not None:
        ui.info(f"Weather API forecast ({launch_site}): {prefetched_qnh:.1f} hPa")

    while True:
        raw = ui.prompt("QNH / sea-level pressure (hPa): ")

        # No empty input — operator must type a number
        if not raw:
            ui.warn("Enter a numeric QNH value. No shortcuts.")
            continue

        # Validate numeric
        try:
            qnh = float(raw)
        except ValueError:
            ui.warn("Enter a numeric QNH value.")
            continue

        # Range check
        if not (QNH_MIN <= qnh <= QNH_MAX):
            ui.warn(f"Outside normal atmospheric range ({QNH_MIN}-{QNH_MAX} hPa).")
            if not ui.prompt_yn("Are you sure?", default=False):
                continue

        # Compare with prefetched — warning only, no choice offered
        if prefetched_qnh is not None:
            delta = abs(qnh - prefetched_qnh)
            if delta > QNH_WARN_DELTA:
                from mercury_config import warnings
                warnings.register(
                    "qnh_delta",
                    f"Entered QNH ({qnh:.1f}) differs from {launch_site} "
                    f"forecast ({prefetched_qnh:.1f}) by {delta:.1f} hPa.",
                )

        qnh_str = f"{qnh:.2f}"
        session_log.log("config", f"QNH set to {qnh_str}")
        ui.success(f"QNH: {qnh_str} hPa")
        return qnh_str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mercury-config && python -m pytest tests/test_config_engine.py -v`
Expected: All tests pass (old + new)

- [ ] **Step 5: Commit**

```bash
git add mercury-config/mercury_config/config_engine.py mercury-config/tests/test_config_engine.py
git commit -m "feat: harden QNH prompt — require explicit numeric entry

Remove Enter-to-keep shortcut. Remove forecast selection shortcut.
Operator must type a numeric value every time. QNH delta warning now
goes through the warning registry for replay at GO gate."
```

---

### Task 7: Revision Cross-Check (`config_engine.py`)

**Files:**
- Modify: `mercury-config/mercury_config/config_engine.py`
- Modify: `mercury-config/tests/test_config_engine.py`

- [ ] **Step 1: Write cross-check tests**

Add to the end of `mercury-config/tests/test_config_engine.py`:

```python
from mercury_config.config_engine import check_revision_crossmatch


class TestRevisionCrossmatch:
    """Detect when device sample_speed contradicts stored revision."""

    @patch("mercury_config.config_engine.warnings")
    def test_rev2_with_correct_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {"sample_speed": "50"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_not_called()

    @patch("mercury_config.config_engine.warnings")
    def test_rev3_with_correct_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(3)
        device_settings = {"sample_speed": "100"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_not_called()

    @patch("mercury_config.config_engine.warnings")
    def test_rev2_with_wrong_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {"sample_speed": "100"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_called_once()
        call_args = mock_warnings.register.call_args
        assert call_args[0][0] == "revision_mismatch"

    @patch("mercury_config.config_engine.warnings")
    def test_missing_sample_speed_no_crash(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {}
        check_revision_crossmatch(golden, device_settings)
        # Missing field is suspicious but not a crossmatch — don't crash
        mock_warnings.register.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mercury-config && python -m pytest tests/test_config_engine.py::TestRevisionCrossmatch -v`
Expected: FAIL — `check_revision_crossmatch` doesn't exist

- [ ] **Step 3: Implement `check_revision_crossmatch()`**

Add after the `values_equal()` function (after line 152) in `mercury-config/mercury_config/config_engine.py`:

```python
def check_revision_crossmatch(
    golden: GoldenConfig,
    device_settings: dict[str, str],
) -> None:
    """Check that device sample_speed matches golden config expectation.

    If the device reports a sample_speed that contradicts the loaded golden
    config, the stored revision may be wrong. Registers a warning — does
    not block.
    """
    if "sample_speed" not in golden.fields:
        return
    expected = golden.fields["sample_speed"].value
    if expected is None:
        return

    actual = device_settings.get("sample_speed")
    if actual is None:
        return  # Field not present — can't cross-check

    if not values_equal(actual, expected):
        from mercury_config import warnings
        warnings.register(
            "revision_mismatch",
            f"Device sample_speed is {actual} but Rev.{golden.revision} "
            f"golden config expects {expected}. Verify the PCB revision "
            f"label is correct.",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mercury-config && python -m pytest tests/test_config_engine.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add mercury-config/mercury_config/config_engine.py mercury-config/tests/test_config_engine.py
git commit -m "feat: add revision crossmatch check

Detects when device sample_speed contradicts the loaded golden config's
expected value, indicating a possible revision mismatch."
```

---

### Task 8: UI Additions (`ui.py`)

**Files:**
- Modify: `mercury-config/mercury_config/ui.py`

No tests for UI functions (they only print — testing would just assert on escape codes). The Phase 9 integration test in Task 10 covers their usage.

- [ ] **Step 1: Add `prompt_exact()` function**

Add after the `prompt_choice()` function (after line 234) in `mercury-config/mercury_config/ui.py`:

```python
def prompt_exact(msg: str, expected: str) -> None:
    """Prompt until the user types the exact expected string.

    Case-sensitive, no shortcuts. Used for ACCEPT and GO gates.
    """
    while True:
        response = prompt(msg)
        if response == expected:
            return
        warn(f"Type {expected} exactly to proceed.")
```

- [ ] **Step 2: Add `flight_readiness_summary()` function**

Add after `prompt_exact()` in `mercury-config/mercury_config/ui.py`:

```python
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
```

- [ ] **Step 3: Add `warning_replay()` function**

Add after `flight_readiness_summary()` in `mercury-config/mercury_config/ui.py`:

```python
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
```

- [ ] **Step 4: Run full test suite**

Run: `cd mercury-config && python -m pytest -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add mercury-config/mercury_config/ui.py
git commit -m "feat: add prompt_exact, flight_readiness_summary, warning_replay

New UI functions for the Phase 9 Flight Readiness Review gate.
prompt_exact() demands an exact string match (ACCEPT, GO).
warning_replay() iterates warnings with per-warning acknowledgement."
```

---

### Task 9: Main Flow Restructure (`main.py`)

**Files:**
- Modify: `mercury-config/mercury_config/main.py`

This is the largest change. The flow is restructured to match the spec. Each sub-step below is a discrete, auditable change within `main.py`.

- [ ] **Step 1: Add new imports at top of `main.py`**

Add to the import block (after line 23 in `mercury-config/mercury_config/main.py`):

```python
from mercury_config import checkpoint
from mercury_config import warnings
```

Note: `weather` is already imported on line 22. No alias needed — the new functions (`get_site_names`, `parse_site_name`, updated `fetch_qnh`) are called as `weather.func()`.

- [ ] **Step 2: Add `launch_site` and checkpoint fields to `_SessionState`**

Replace `_SessionState.__init__` (lines 29-36) with:

```python
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
```

- [ ] **Step 3: Add taint check to Phase 0**

In `_run()`, after the environment check block (after line 144), add:

```python
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
```

- [ ] **Step 4: Add launch site selection to Phase 0**

After the taint check block (and before QNH pre-fetch), add:

```python
    # 0.6 — Launch site selection
    ui.section("LAUNCH SITE")
    site_display = ui.prompt_choice(
        "Select launch site",
        weather.get_site_names(),
    )
    launch_site = weather.parse_site_name(site_display)
    _state.launch_site = launch_site
    session_log.log("session", f"Launch site: {launch_site}")
```

- [ ] **Step 5: Update QNH pre-fetch to use launch site**

Replace the QNH pre-fetch block (lines 148-154) with:

```python
    # 0.7 — Pre-fetch QNH (if internet available)
    prefetched_qnh: float | None = None
    if internet_available:
        ui.info(f"Pre-fetching QNH for {launch_site}...")
        prefetched_qnh = weather.fetch_qnh(launch_site)
        if prefetched_qnh is not None:
            ui.success(f"QNH forecast ({launch_site}): {prefetched_qnh:.1f} hPa")
        else:
            ui.warn("QNH fetch failed — you'll need to enter it manually")
```

- [ ] **Step 6: Replace revision logic in Phase 2**

Replace the current revision block (lines 197-200):

```python
    # 2.6 — Hardware revision
    revision = cdc.ask_hardware_revision()

    # 2.9 — Print device identity
    ui.device_identity(device_serial or "unknown", revision, firmware)
```

With:

```python
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
```

- [ ] **Step 7: Create taint checkpoint at Phase 2**

After the device identity print, add:

```python
    # 2.10 — Create taint checkpoint
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
```

- [ ] **Step 8: Add cross-device taint warning after Phase 2 identity**

After the checkpoint write, add:

```python
    # 2.11 — Cross-device taint warning
    for stale in stale_sessions:
        if stale["serial"] != device_serial:
            warnings.register(
                "tainted_device",
                f"Unresolved session for {stale['serial']}. That device "
                f"may have unverified config.",
            )
```

- [ ] **Step 9: Add revision crossmatch after Phase 5**

After the field count validation (after line 317), add:

```python
    # 5.3 — Revision crossmatch check
    config_engine.check_revision_crossmatch(golden, device_settings)
```

- [ ] **Step 10: Update Phase 7 QNH call**

Replace line 342:

```python
    qnh_value = config_engine.prompt_qnh(current_qnh, prefetched_qnh)
```

With:

```python
    qnh_value = config_engine.prompt_qnh(current_qnh, prefetched_qnh, launch_site)
```

- [ ] **Step 11: Update checkpoint after Phase 8 push/verify**

After the push_and_verify call (after line 373), add checkpoint updates. Find the block:

```python
    if not (_state.config_pushed and _state.config_verified):
        _state.config_pushed = True
```

And after `_state.config_pushed = True`, add:

```python
        if device_serial:
            checkpoint.update(device_serial, {
                "phase_reached": "phase8_push",
                "config_pushed": True,
                "warnings": warnings.serialise(),
            })
```

And after `_state.config_verified = verified` (when first verify completes), add:

```python
        if device_serial and verified:
            checkpoint.update(device_serial, {
                "phase_reached": "phase8_verified",
                "first_verify_passed": True,
                "qnh_value": qnh_value,
                "warnings": warnings.serialise(),
            })
```

- [ ] **Step 12: Replace Phase 9 with new flow (Phase 8.5, 8.6, 9)**

Replace everything from `# --- Phase 9: Flight Readiness ---` (line 396) to the end of `_run()` (line 412) with:

```python
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
```

- [ ] **Step 13: Run full test suite**

Run: `cd mercury-config && python -m pytest -v`
Expected: All tests pass

- [ ] **Step 14: Commit**

```bash
git add mercury-config/mercury_config/main.py
git commit -m "feat: restructure flow for operator-absent hardening

- Phase 0: taint check + launch site selection
- Phase 2: revision from managed devices + Rev.2 warning + checkpoint
- Phase 5: revision crossmatch check
- Phase 7: QNH with launch site
- Phase 8.5: optional browser calibration with warning
- Phase 8.6: mandatory second verification pass
- Phase 9: Flight Readiness Review (summary, warning replay with
  ACCEPT, airframe designation, typed GO)
- Taint checkpoint updated at each phase boundary, deleted on GO"
```

---

### Task 10: Integration Smoke Test

**Files:**
- None created — this is a manual verification step

- [ ] **Step 1: Run full test suite one final time**

Run: `cd mercury-config && python -m pytest -v`
Expected: All tests pass (existing + new)

- [ ] **Step 2: Verify grepability of warning registrations**

Run: `cd mercury-config && grep -rn "warnings.register" mercury_config/`
Expected: Should show all registration call sites:
- `warnings.py` (the register function itself)
- `main.py` (rev2, tainted_device, browser warnings)
- `config_engine.py` (qnh_delta, revision_mismatch)

Verify every one makes sense and none are missing.

- [ ] **Step 3: Verify no stale `weather.fetch_qnh()` calls without site parameter**

Run: `cd mercury-config && grep -rn "fetch_qnh" mercury_config/`
Expected: Only `weather.py` (definition) and `main.py` (with site parameter). No bare `fetch_qnh()` calls.

- [ ] **Step 4: Verify no remaining `Enter = keep` patterns**

Run: `cd mercury-config && grep -rn "Enter = keep\|Enter-to-keep" mercury_config/`
Expected: No matches.

- [ ] **Step 5: Commit integration verification**

No code changes — this is a verification-only step. If everything passes, the implementation is complete.

```bash
git commit --allow-empty -m "chore: verified operator-absent hardening integration

All tests pass. Warning registration sites verified. No stale API calls.
No remaining Enter-to-keep patterns."
```
