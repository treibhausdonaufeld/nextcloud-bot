from datetime import datetime, timedelta

import pytest

from app.services import storm_alert
from app.services.config import StormAlertConfig


def make_forecast(hours):
    """Build an Open-Meteo-style hourly block from a list of wind speeds."""
    now = datetime.now()
    times = [
        (now + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(hours))
    ]
    return {"time": times, "wind_speed_10m": list(hours)}


@pytest.fixture
def alert():
    return storm_alert.StormAlert(
        StormAlertConfig(enabled=True, channel="allgemein", forecast_hours=24)
    )


def patch_send(monkeypatch):
    sent = []
    monkeypatch.setattr(
        storm_alert, "send_message", lambda text, channel: sent.append((text, channel))
    )
    return sent


def test_alerts_when_wind_exceeds_threshold(monkeypatch, alert):
    monkeypatch.setattr(storm_alert, "get_state", lambda key: None)
    monkeypatch.setattr(storm_alert, "set_state", lambda key, value: None)
    monkeypatch.setattr(
        storm_alert.StormAlert,
        "fetch_forecast",
        lambda self: make_forecast([10, 50, 30, 20]),
    )
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent, "expected a storm alarm"
    assert sent[0][1] == "allgemein"
    assert "50" in sent[0][0]


def test_no_alert_when_below_threshold(monkeypatch, alert):
    monkeypatch.setattr(storm_alert, "get_state", lambda key: None)
    monkeypatch.setattr(storm_alert, "set_state", lambda key, value: None)
    monkeypatch.setattr(
        storm_alert.StormAlert,
        "fetch_forecast",
        lambda self: make_forecast([10, 20, 30]),
    )
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent == []


def test_disabled_does_not_alert(monkeypatch, alert):
    alert.config.enabled = False
    monkeypatch.setattr(
        storm_alert.StormAlert, "fetch_forecast", lambda self: make_forecast([100])
    )
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent == []


def test_dedup_per_day(monkeypatch, alert):
    monkeypatch.setattr(
        storm_alert, "get_state", lambda key: datetime.now().date().isoformat()
    )
    monkeypatch.setattr(storm_alert, "set_state", lambda key, value: None)
    monkeypatch.setattr(
        storm_alert.StormAlert,
        "fetch_forecast",
        lambda self: make_forecast([10, 60, 20]),
    )
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent == []


def test_alert_on_new_day(monkeypatch, alert):
    monkeypatch.setattr(storm_alert, "get_state", lambda key: "2000-01-01")
    monkeypatch.setattr(storm_alert, "set_state", lambda key, value: None)
    monkeypatch.setattr(
        storm_alert.StormAlert,
        "fetch_forecast",
        lambda self: make_forecast([10, 60, 20]),
    )
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent, "expected an alarm once the previous day has passed"


def test_hours_outside_window_are_ignored(monkeypatch, alert):
    now = datetime.now()
    times = [(now + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M")]
    monkeypatch.setattr(
        storm_alert.StormAlert,
        "fetch_forecast",
        lambda self: {"time": times, "wind_speed_10m": [80]},
    )
    monkeypatch.setattr(storm_alert, "get_state", lambda key: None)
    monkeypatch.setattr(storm_alert, "set_state", lambda key, value: None)
    sent = patch_send(monkeypatch)

    alert.check_forecast()

    assert sent == []
