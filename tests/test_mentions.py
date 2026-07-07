"""Tests for the mentions page look-back filter."""

from datetime import datetime

from app.controllers.mentions import months_ago_epoch


class TestMonthsAgoEpoch:
    def test_plain_subtraction(self):
        now = datetime(2026, 7, 7, 12, 30)
        assert months_ago_epoch(12, now) == int(
            datetime(2025, 7, 7, 12, 30).timestamp()
        )

    def test_year_rollover(self):
        now = datetime(2026, 3, 15)
        assert months_ago_epoch(6, now) == int(datetime(2025, 9, 15).timestamp())

    def test_day_clamped_to_shorter_month(self):
        now = datetime(2026, 5, 31)
        assert months_ago_epoch(1, now) == int(datetime(2026, 4, 30).timestamp())

    def test_day_clamped_to_february(self):
        now = datetime(2026, 3, 30)
        assert months_ago_epoch(1, now) == int(datetime(2026, 2, 28).timestamp())

    def test_leap_year_february(self):
        now = datetime(2024, 3, 30)
        assert months_ago_epoch(1, now) == int(datetime(2024, 2, 29).timestamp())

    def test_december_target(self):
        now = datetime(2026, 1, 31)
        assert months_ago_epoch(1, now) == int(datetime(2025, 12, 31).timestamp())
