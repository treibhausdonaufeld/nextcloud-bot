"""Storm warning for Vienna sent to Rocket.Chat and Matrix.

Periodically fetches the wind forecast for a configured location (Vienna by
default) from Open-Meteo and, whenever the forecast predicts wind above the
configured threshold (45 km/h by default), posts an alarm to the configured
notification channel. The alarm goes out through :mod:`app.services.notify`,
which delivers to both Matrix and Rocket.Chat while both are configured.

Alerts are de-duplicated per local day so the message is not re-sent on every
worker iteration while the storm is still in the forecast.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from app.models.kv import get_state, set_state
from app.services.config import StormAlertConfig
from app.services.notify import send_message

logger = logging.getLogger(__name__)

# Open-Meteo is free and needs no API key.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Key under which the last alerted local day is remembered, so the same storm
# is not announced again on every worker iteration.
STATE_KEY = "storm_alert_last_alerted_date"


class StormAlert:
    def __init__(self, config: StormAlertConfig) -> None:
        self.config = config

    def check_forecast(self) -> None:
        """Fetch the forecast and alarm when storm winds are predicted."""
        if not self.config.enabled:
            return

        forecast = self.fetch_forecast()
        if forecast is None:
            return

        peak_time, peak_speed = self.find_storm(forecast)
        if peak_time is None or peak_speed is None:
            logger.debug("No storm wind predicted for %s", self.config.location_name)
            return

        # De-dupe: only alarm once per local day.
        today = date.today()
        if get_state(STATE_KEY) == today.isoformat():
            logger.info("Storm already alerted for %s, skipping", today)
            return

        self.send_alert(peak_time, peak_speed)
        set_state(STATE_KEY, today.isoformat())

    def fetch_forecast(self) -> Optional[dict[str, Any]]:
        """Return the Open-Meteo hourly block, or ``None`` on failure.

        Open-Meteo needs no API key; wind speed comes back in km/h by default,
        which is exactly the unit the threshold is expressed in.
        """
        params: dict[str, Any] = {
            "latitude": self.config.latitude,
            "longitude": self.config.longitude,
            "hourly": "wind_speed_10m",
            "forecast_days": 1,
            "timezone": self.config.timezone,
        }
        try:
            response = requests.get(FORECAST_URL, params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException:
            logger.exception(
                "Failed to fetch wind forecast for %s", self.config.location_name
            )
            return None

        data = response.json().get("hourly") or {}
        times = data.get("time")
        speeds = data.get("wind_speed_10m")
        if not times or speeds is None or len(times) != len(speeds):
            logger.warning(
                "Open-Meteo returned an unexpected payload for %s",
                self.config.location_name,
            )
            return None

        return {"time": list(times), "wind_speed_10m": list(speeds)}

    def find_storm(
        self, forecast: dict[str, Any]
    ) -> tuple[Optional[datetime], Optional[float]]:
        """The hour of the peak wind speed, if it exceeds the threshold.

        Only hours within the next ``forecast_hours`` are considered. Returns
        ``(None, None)`` when no hour in the window crosses the threshold.
        """
        threshold = self.config.wind_threshold_kmh
        now = datetime.now()
        limit = now + timedelta(hours=self.config.forecast_hours)

        peak_time: Optional[datetime] = None
        peak_speed: Optional[float] = None
        for t, speed in zip(forecast["time"], forecast["wind_speed_10m"]):
            try:
                hour = datetime.fromisoformat(t)
            except ValueError:
                continue
            if hour < now or hour > limit:
                continue
            try:
                speed_val = float(speed)
            except (TypeError, ValueError):
                continue
            if speed_val > threshold and (peak_speed is None or speed_val > peak_speed):
                peak_time = hour
                peak_speed = speed_val

        return peak_time, peak_speed

    def send_alert(self, peak_time: datetime, peak_speed: float) -> None:
        """Post the storm alarm to the configured channel."""
        text = (
            f"🌩️ **Sturmwarnung für {self.config.location_name}**\n"
            f"Für die nächsten {self.config.forecast_hours} Stunden sind "
            f"Windböen über {self.config.wind_threshold_kmh:g} km/h vorhergesagt.\n"
            f"- Spitzenwindgeschwindigkeit: **{peak_speed:g} km/h** "
            f"(erwartet um {peak_time.strftime('%d.%m.%Y, %H:%M')} Uhr)"
        )
        send_message(text, self.config.channel)
