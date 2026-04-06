"""Best-effort QNH pre-fetch from Open-Meteo weather API.

Uses Open-Meteo (no API key required, free tier). Falls back gracefully —
this is a convenience, not a dependency. The user always confirms or
overrides the value.
"""

from __future__ import annotations

from mercury_config import session_log

# Open-Meteo: free, no API key, returns surface pressure.
# We use a central UK location as a reasonable default.
# Surface pressure at station level != sea-level pressure (QNH),
# but Open-Meteo's "current_weather" includes pressure_msl.
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PARAMS = {
    "latitude": "52.0",    # Approx central UK
    "longitude": "-1.0",
    "current": "pressure_msl",
    "forecast_days": "1",
}

FETCH_TIMEOUT_S = 5


def fetch_qnh() -> float | None:
    """Fetch current sea-level pressure (QNH) from Open-Meteo.

    Returns:
        Pressure in hPa, or None on any failure.
    """
    try:
        import requests

        session_log.log("weather", "Fetching QNH from Open-Meteo...")
        response = requests.get(
            OPEN_METEO_URL,
            params=OPEN_METEO_PARAMS,
            timeout=FETCH_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()

        pressure = data.get("current", {}).get("pressure_msl")
        if pressure is not None:
            qnh = float(pressure)
            session_log.log("weather", f"QNH from API: {qnh} hPa")
            return qnh

        session_log.log("weather", f"No pressure_msl in response: {data}")
        return None

    except Exception as e:
        session_log.log("weather", f"QNH fetch failed: {e}")
        return None
