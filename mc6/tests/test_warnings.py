"""Tests for the flight-safety warning registry.

The warning registry is the backbone of the GO gate — every off-nominal
condition passes through it. A bug here means warnings silently vanish.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc6 import warnings


class TestRegister:
    """register() must print, log, and store."""

    def setup_method(self) -> None:
        warnings.clear()

    def test_register_stores_warning(self) -> None:
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
            warnings.register("test_cat", "test message")
        assert warnings.count() == 1
        stored = warnings.get_all()
        assert stored[0] == ("test_cat", "test message")

    def test_register_calls_ui_warn(self) -> None:
        with patch("mc6.warnings.ui") as mock_ui, \
             patch("mc6.warnings.session_log"):
            warnings.register("cat", "something bad")
        mock_ui.warn.assert_called_once_with("something bad")

    def test_register_calls_session_log(self) -> None:
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log") as mock_log:
            warnings.register("cat", "something bad")
        mock_log.log.assert_called_once_with("warning", "[cat] something bad")

    def test_get_all_returns_copy(self) -> None:
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
            warnings.register("a", "first")
        result = warnings.get_all()
        result.append(("injected", "should not appear"))
        assert warnings.count() == 1

    def test_multiple_warnings_accumulate(self) -> None:
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
            warnings.register("a", "first")
            warnings.register("b", "second")
            warnings.register("c", "third")
        assert warnings.count() == 3
        all_warnings = warnings.get_all()
        assert all_warnings[0] == ("a", "first")
        assert all_warnings[1] == ("b", "second")
        assert all_warnings[2] == ("c", "third")


class TestClear:
    def setup_method(self) -> None:
        warnings.clear()

    def test_clear_empties_registry(self) -> None:
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
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
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
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

    def test_deserialise_replaces_existing(self) -> None:
        """deserialise() must clear existing warnings, not append."""
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
            warnings.register("old", "should be replaced")
        warnings.deserialise([{"category": "new", "message": "fresh"}])
        assert warnings.count() == 1
        assert warnings.get_all()[0] == ("new", "fresh")

    def test_deserialise_rejects_malformed(self) -> None:
        """Malformed data must not partially populate warnings."""
        with patch("mc6.warnings.ui"), \
             patch("mc6.warnings.session_log"):
            warnings.register("existing", "should survive")
        import pytest
        with pytest.raises(ValueError, match="Malformed warning"):
            warnings.deserialise([
                {"category": "ok", "message": "fine"},
                {"bad_key": "no category field"},
            ])
        # Atomic: validation failed so _warnings is untouched
        assert warnings.count() == 1
        assert warnings.get_all()[0] == ("existing", "should survive")
