"""Tests for launch site selection and site-specific QNH fetch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc6.weather import LAUNCH_SITES, get_site_names, get_site_coords


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
