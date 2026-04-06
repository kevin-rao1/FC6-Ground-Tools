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
