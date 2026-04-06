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
