"""Tests for the Mercury HTML form parser.

The parser must correctly handle Mercury's embedded web server HTML, which
uses unclosed <option> tags. A parser bug here means we read the wrong
config state from the device and either:
1. Fail to detect a misconfiguration (false match), or
2. Overwrite correct values with wrong ones (read-modify-write corruption).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc6.http_config import _MercuryFormParser, _regex_parse_form


# Representative Mercury /settings/ HTML snippet with unclosed option tags
SETTINGS_SNIPPET = """
<html><body>
<form method="get" action="/settings/">
<input type="hidden" name="sb" value="y">
<input type="text" name="wifiname" value="MercuryAlt_4679">
<input type="text" name="wifipass" value="05c69008">
<input type="number" name="sealevel" value="1013.25">
<select name="uart" class="inputbox">
<option value="0">Off
<option value="1" selected>On
</select>
<select name="sample_speed" class="inputbox">
<option value="50" selected>50 Hz
<option value="100">100 Hz
</select>
<select name="sample_ratio" class="inputbox">
<option value="10000">Hybrid 1/3
<option value="10005">Hybrid 2/3
<option value="1" selected>1:1
</select>
<select name="unit_acc" class="inputbox">
<option value="1">mG
<option value="2" selected>m/s2
<option value="3">G
</select>
<select name="anglefilter" class="inputbox">
<option value="0">Mahony
<option value="1" selected>Madgwick
</select>
<input type="hidden" name="adjustlaunchangle" value="0">
<input type="number" name="lockout_time" value="500.00">
<input type="number" name="lockout_change" value="1.00">
<input type="number" name="fixed_temp" value="15.00">
<input type="submit" value="Save changes">
</form>
</body></html>
"""

# Snippet with NO selected attribute on a select
NO_SELECTED_SNIPPET = """
<form>
<select name="aftersave" class="inputbox">
<option value="0">Reboot
<option value="1">Stay
</select>
</form>
"""


class TestMercuryFormParser:
    """Test the HTMLParser-based form extractor."""

    def _parse(self, html: str) -> dict[str, str]:
        parser = _MercuryFormParser()
        parser.feed(html)
        return parser.fields

    def test_extracts_inputs(self) -> None:
        fields = self._parse(SETTINGS_SNIPPET)
        assert fields["wifiname"] == "MercuryAlt_4679"
        assert fields["wifipass"] == "05c69008"
        assert fields["sealevel"] == "1013.25"
        assert fields["adjustlaunchangle"] == "0"

    def test_extracts_hidden_sb(self) -> None:
        fields = self._parse(SETTINGS_SNIPPET)
        assert fields["sb"] == "y"

    def test_extracts_selected_options(self) -> None:
        fields = self._parse(SETTINGS_SNIPPET)
        assert fields["uart"] == "1"
        assert fields["sample_speed"] == "50"
        assert fields["sample_ratio"] == "1"
        assert fields["unit_acc"] == "2"
        assert fields["anglefilter"] == "1"

    def test_number_input_values(self) -> None:
        fields = self._parse(SETTINGS_SNIPPET)
        assert fields["lockout_time"] == "500.00"
        assert fields["lockout_change"] == "1.00"
        assert fields["fixed_temp"] == "15.00"

    def test_no_selected_falls_back_to_first_option(self) -> None:
        fields = self._parse(NO_SELECTED_SNIPPET)
        assert fields["aftersave"] == "0"

    def test_unnamed_inputs_skipped(self) -> None:
        """Submit buttons have no name — they should not appear in fields."""
        fields = self._parse(SETTINGS_SNIPPET)
        assert "" not in fields

    def test_field_count(self) -> None:
        """Sanity check: we should find all fields in the snippet."""
        fields = self._parse(SETTINGS_SNIPPET)
        # 7 named inputs (sb, wifiname, wifipass, sealevel, adjustlaunchangle,
        #                  lockout_time, lockout_change, fixed_temp) = 8
        # 5 selects (uart, sample_speed, sample_ratio, unit_acc, anglefilter)
        # Submit button has no name — excluded
        assert len(fields) >= 13


class TestRegexFallbackParser:
    """Test the regex-based fallback parser against the same snippets."""

    def test_extracts_inputs(self) -> None:
        fields = _regex_parse_form(SETTINGS_SNIPPET)
        assert fields["wifiname"] == "MercuryAlt_4679"
        assert fields["sealevel"] == "1013.25"

    def test_extracts_selected_options(self) -> None:
        fields = _regex_parse_form(SETTINGS_SNIPPET)
        assert fields["uart"] == "1"
        assert fields["sample_speed"] == "50"
        assert fields["sample_ratio"] == "1"

    def test_no_selected_falls_back_to_first(self) -> None:
        fields = _regex_parse_form(NO_SELECTED_SNIPPET)
        assert fields["aftersave"] == "0"


class TestParserAgainstRealHTML:
    """Parse the actual Mercury HTML dumps if available.

    These tests are skipped if the dump files are not present (e.g. in CI).
    """

    SETTINGS_PATH = Path(__file__).resolve().parent.parent / (
        "Altimetercloud.com AltimeterAltimeter.html"
    )
    OUTPUTS_PATH = Path(__file__).resolve().parent.parent / (
        "Altimetercloud.com AltimeterAltimeter outputs.html"
    )

    @pytest.fixture
    def settings_html(self) -> str:
        assert self.SETTINGS_PATH.exists(), (
            f"Settings HTML dump missing: {self.SETTINGS_PATH}"
        )
        return self.SETTINGS_PATH.read_text()

    @pytest.fixture
    def outputs_html(self) -> str:
        assert self.OUTPUTS_PATH.exists(), (
            f"Outputs HTML dump missing: {self.OUTPUTS_PATH}"
        )
        return self.OUTPUTS_PATH.read_text()

    def test_settings_field_count(self, settings_html: str) -> None:
        parser = _MercuryFormParser()
        parser.feed(settings_html)
        # The settings page has ~46 fields (inputs + selects)
        assert len(parser.fields) >= 30, (
            f"Only {len(parser.fields)} fields parsed — expected >= 30"
        )

    def test_settings_known_values(self, settings_html: str) -> None:
        """Verify parser reads known default values from the real HTML."""
        parser = _MercuryFormParser()
        parser.feed(settings_html)
        f = parser.fields

        assert f.get("wifiname") == "MercuryAlt_4679"
        assert f.get("wifipass") == "05c69008"
        assert f.get("sealevel") == "1013.25"
        assert f.get("uart") == "0"  # Default is disabled
        assert f.get("sample_speed") == "50"
        assert f.get("oversampling") == "8"
        assert f.get("kalmanfilter") == "3"
        assert f.get("anglefilter") == "1"
        assert f.get("sample_ratio") == "10000"  # Default is Hybrid 1/3
        assert f.get("adjustlaunchangle") == "0"

    def test_outputs_has_calc_density(self, outputs_html: str) -> None:
        parser = _MercuryFormParser()
        parser.feed(outputs_html)
        assert "calc_density" in parser.fields
        assert parser.fields["calc_density"] == "1"

    def test_outputs_field_count(self, outputs_html: str) -> None:
        parser = _MercuryFormParser()
        parser.feed(outputs_html)
        # /outputs/ has many fields (rules, servos, airbrake, etc.)
        assert len(parser.fields) >= 50, (
            f"Only {len(parser.fields)} fields parsed — expected >= 50"
        )
