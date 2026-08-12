"""Timestamps must render in the configured timezone, not the container's."""

import os
import time
from unittest.mock import patch

import pytest

from app.models.base import format_date, format_timestamp
from app.settings import settings

# 2025-01-01 00:30 UTC — the same instant is still 2024-12-31 in New York,
# so a conversion that uses the system timezone shows a different day.
NEW_YEAR_UTC = 1735691400


@pytest.fixture
def system_tz_new_york():
    """Run the test as if the container ran in America/New_York."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if previous is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = previous
    time.tzset()


class TestFormatTimestamp:
    def test_uses_the_configured_timezone(self, system_tz_new_york):
        with patch.object(settings, "timezone", "Europe/Berlin"):
            assert format_timestamp(NEW_YEAR_UTC) == "Wed Jan  1 01:30:00 2025"

    def test_other_timezones_shift_accordingly(self, system_tz_new_york):
        with patch.object(settings, "timezone", "UTC"):
            assert format_timestamp(NEW_YEAR_UTC) == "Wed Jan  1 00:30:00 2025"

    def test_missing_timestamp_is_none(self):
        assert format_timestamp(None) is None
        assert format_timestamp(0) is None


class TestFormatDate:
    def test_uses_the_configured_timezone(self, system_tz_new_york):
        with patch.object(settings, "timezone", "Europe/Berlin"):
            assert format_date(NEW_YEAR_UTC) == "2025-01-01"

    def test_other_timezones_shift_accordingly(self, system_tz_new_york):
        with patch.object(settings, "timezone", "America/New_York"):
            assert format_date(NEW_YEAR_UTC) == "2024-12-31"

    def test_missing_timestamp_is_none(self):
        assert format_date(None) is None
        assert format_date(0) is None
