"""Tests for config_engine: values_equal, golden config loading, build_write_payload.

These tests guard the most safety-critical code paths — the ones where a
silent bug could misconfigure flight hardware.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, call

import pytest

# Ensure the package is importable from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc6 import config_engine
from mc6.config_engine import (
    GoldenConfig,
    build_write_payload,
    diff_config,
    load_golden,
    values_equal,
)


# --- values_equal ---

class TestValuesEqual:
    """values_equal is used to verify every config field after write.
    A false positive (says equal when not) = undetected misconfiguration.
    A false negative (says different when equal) = spurious verification failure.
    """

    def test_identical_strings(self) -> None:
        assert values_equal("1400", "1400")

    def test_float_formatting_1(self) -> None:
        """Mercury may return "1.00" but golden has "1.0"."""
        assert values_equal("1.00", "1.0")

    def test_float_formatting_2(self) -> None:
        """Mercury may return "500.00" but golden has "500"."""
        assert values_equal("500.00", "500")

    def test_float_formatting_3(self) -> None:
        assert values_equal("0.00", "0")

    def test_float_formatting_4(self) -> None:
        assert values_equal("15.00", "15.0")

    def test_different_values(self) -> None:
        """Must NOT match when values are actually different."""
        assert not values_equal("50", "100")

    def test_different_float_values(self) -> None:
        assert not values_equal("1.00", "1.50")

    def test_string_vs_int_different(self) -> None:
        assert not values_equal("0", "1")

    def test_empty_vs_value(self) -> None:
        assert not values_equal("", "1")

    def test_non_numeric_equal(self) -> None:
        assert values_equal("MercuryAlt_4679", "MercuryAlt_4679")

    def test_non_numeric_different(self) -> None:
        assert not values_equal("MercuryAlt_4679", "MercuryAlt_1234")

    def test_trailing_whitespace(self) -> None:
        """Trailing whitespace is stripped by float() — values still match.
        This is correct: whitespace in parsed HTML values is an artifact."""
        assert values_equal("1400 ", "1400")


# --- Golden config loading ---

class TestGoldenConfig:
    """Golden configs must load without error and contain the correct values."""

    @pytest.fixture(params=[2, 3])
    def golden(self, request: pytest.FixtureRequest) -> GoldenConfig:
        return load_golden(request.param)

    def test_loads_without_error(self, golden: GoldenConfig) -> None:
        assert golden.revision in (2, 3)

    def test_has_expected_fixed_fields(self, golden: GoldenConfig) -> None:
        fixed = [
            name for name, info in golden.fields.items()
            if info.field_class == "fixed"
        ]
        # 25 fixed fields per the spec
        assert len(fixed) == 25

    def test_has_volatile_field(self, golden: GoldenConfig) -> None:
        volatile = [
            name for name, info in golden.fields.items()
            if info.field_class == "volatile"
        ]
        assert volatile == ["sealevel"]

    def test_has_identity_fields(self, golden: GoldenConfig) -> None:
        identity = {
            name for name, info in golden.fields.items()
            if info.field_class == "identity"
        }
        assert identity == {"wifiname", "wifipass"}

    def test_uart_enabled(self, golden: GoldenConfig) -> None:
        assert golden.fields["uart"].value == "1"

    def test_sample_ratio_override(self, golden: GoldenConfig) -> None:
        assert golden.fields["sample_ratio"].value == "1"

    def test_calc_density_enabled(self, golden: GoldenConfig) -> None:
        """FC6 does NOT compute its own density — Mercury must."""
        assert golden.fields["calc_density"].value == "1"
        assert golden.fields["calc_density"].endpoint == "/outputs/"

    def test_sample_speed_rev_dependent(self) -> None:
        rev2 = load_golden(2)
        rev3 = load_golden(3)
        assert rev2.fields["sample_speed"].value == "50"
        assert rev3.fields["sample_speed"].value == "100"

    def test_all_fixed_have_values(self, golden: GoldenConfig) -> None:
        for name, info in golden.fields.items():
            if info.field_class == "fixed":
                assert info.value is not None, f"Fixed field {name} has no value"

    def test_all_endpoints_valid(self, golden: GoldenConfig) -> None:
        for name, info in golden.fields.items():
            assert info.endpoint in ("/settings/", "/outputs/"), (
                f"Field {name} has invalid endpoint: {info.endpoint}"
            )


# --- build_write_payload ---

class TestBuildWritePayload:
    """build_write_payload must:
    1. Preserve ALL device fields (read-modify-write).
    2. Overlay golden config values for fixed fields.
    3. Apply explicit overrides.
    4. NOT drop any device fields.
    """

    def _make_golden(self) -> GoldenConfig:
        return load_golden(2)

    def test_preserves_all_device_fields(self) -> None:
        golden = self._make_golden()
        device_fields = {
            "uart": "0",
            "sealevel": "1013.25",
            "wifiname": "MercuryAlt_4679",
            "language": "en",
            "shutdown": "1",
            "adjustlaunchangle": "0",
            "roc2_en": "0",
            "roc2_o1_en": "0",
            "some_unknown_field": "42",
        }
        payload = build_write_payload(golden, device_fields, {}, "/settings/")

        # ALL original fields must be present
        for key in device_fields:
            assert key in payload, f"Field {key} was dropped"

    def test_overlays_golden_values(self) -> None:
        golden = self._make_golden()
        device_fields = {
            "uart": "0",  # Wrong — golden says 1
            "sample_ratio": "10000",  # Wrong — golden says 1
            "sealevel": "1013.25",
            "wifiname": "MercuryAlt_4679",
        }
        payload = build_write_payload(golden, device_fields, {}, "/settings/")

        assert payload["uart"] == "1", "Golden override not applied"
        assert payload["sample_ratio"] == "1", "Golden override not applied"

    def test_applies_explicit_overrides(self) -> None:
        golden = self._make_golden()
        device_fields = {"sealevel": "1013.25", "uart": "1"}
        overrides = {"sealevel": "1025.00"}
        payload = build_write_payload(golden, device_fields, overrides, "/settings/")

        assert payload["sealevel"] == "1025.00"

    def test_identity_fields_not_overwritten(self) -> None:
        golden = self._make_golden()
        device_fields = {
            "wifiname": "MercuryAlt_4679",
            "wifipass": "mypassword",
            "uart": "0",
        }
        payload = build_write_payload(golden, device_fields, {}, "/settings/")

        # Identity fields should keep device values
        assert payload["wifiname"] == "MercuryAlt_4679"
        assert payload["wifipass"] == "mypassword"

    def test_outputs_endpoint_fields_not_applied_to_settings(self) -> None:
        golden = self._make_golden()
        device_fields = {"uart": "0"}
        payload = build_write_payload(golden, device_fields, {}, "/settings/")

        # calc_density is on /outputs/, should NOT appear in /settings/ payload
        # unless it was already in device_fields
        assert "calc_density" not in payload

    def test_calc_density_applied_to_outputs(self) -> None:
        golden = self._make_golden()
        device_fields = {"calc_density": "0", "airbrake": "0"}
        payload = build_write_payload(golden, device_fields, {}, "/outputs/")

        assert payload["calc_density"] == "1"  # Golden overrides device's 0
        assert payload["airbrake"] == "0"  # Non-golden field preserved


# --- diff_config ---

class TestDiffConfig:
    def test_all_matching(self) -> None:
        golden = load_golden(2)
        settings = {
            name: info.value
            for name, info in golden.fields.items()
            if info.endpoint == "/settings/" and info.value is not None
        }
        settings["sealevel"] = "1013.25"
        settings["wifiname"] = "MercuryAlt_4679"
        settings["wifipass"] = "05c69008"

        outputs = {
            name: info.value
            for name, info in golden.fields.items()
            if info.endpoint == "/outputs/" and info.value is not None
        }

        diff = diff_config(golden, settings, outputs)
        mismatches = [d for d in diff if d.field_class == "fixed" and not d.matches]
        assert len(mismatches) == 0

    def test_detects_mismatch(self) -> None:
        golden = load_golden(2)
        settings = {
            name: info.value
            for name, info in golden.fields.items()
            if info.endpoint == "/settings/" and info.value is not None
        }
        settings["sealevel"] = "1013.25"
        settings["wifiname"] = "MercuryAlt_4679"
        settings["wifipass"] = "05c69008"
        settings["uart"] = "0"  # Deliberate mismatch

        outputs = {"calc_density": "1"}

        diff = diff_config(golden, settings, outputs)
        mismatches = [d for d in diff if d.field_class == "fixed" and not d.matches]
        assert len(mismatches) == 1
        assert mismatches[0].name == "uart"
        assert mismatches[0].actual == "0"
        assert mismatches[0].expected == "1"


class TestPromptQnhHardened:
    """QNH prompt must reject empty input and require explicit numeric entry."""

    @patch("mc6.config_engine.ui")
    def test_rejects_empty_input(self, mock_ui) -> None:
        """Enter-to-keep must NOT be accepted."""
        mock_ui.prompt.side_effect = ["", "1013.25"]
        mock_ui.warn = lambda msg: None
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mc6.config_engine.session_log"):
            result = config_engine.prompt_qnh(
                current_value="1010.00",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1013.25"
        assert mock_ui.prompt.call_count == 2

    @patch("mc6.config_engine.ui")
    def test_accepts_valid_numeric(self, mock_ui) -> None:
        mock_ui.prompt.return_value = "1025.50"
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mc6.config_engine.session_log"):
            result = config_engine.prompt_qnh(
                current_value="1013.25",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1025.50"

    @patch("mc6.config_engine.ui")
    def test_rejects_non_numeric(self, mock_ui) -> None:
        mock_ui.prompt.side_effect = ["abc", "1013.25"]
        mock_ui.warn = lambda msg: None
        mock_ui.info = lambda msg: None
        mock_ui.success = lambda msg: None
        mock_ui.section = lambda msg: None

        with patch("mc6.config_engine.session_log"):
            result = config_engine.prompt_qnh(
                current_value="1010.00",
                prefetched_qnh=None,
                launch_site="Cox's Field",
            )
        assert result == "1013.25"
        assert mock_ui.prompt.call_count == 2


from mc6.config_engine import check_revision_crossmatch


class TestRevisionCrossmatch:
    """Detect when device sample_speed contradicts stored revision."""

    @patch("mc6.warnings")
    def test_rev2_with_correct_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {"sample_speed": "50"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_not_called()

    @patch("mc6.warnings")
    def test_rev3_with_correct_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(3)
        device_settings = {"sample_speed": "100"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_not_called()

    @patch("mc6.warnings")
    def test_rev2_with_wrong_sample_speed(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {"sample_speed": "100"}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_called_once()
        call_args = mock_warnings.register.call_args
        assert call_args[0][0] == "revision_mismatch"

    @patch("mc6.warnings")
    def test_missing_sample_speed_no_crash(self, mock_warnings) -> None:
        golden = load_golden(2)
        device_settings = {}
        check_revision_crossmatch(golden, device_settings)
        mock_warnings.register.assert_not_called()
