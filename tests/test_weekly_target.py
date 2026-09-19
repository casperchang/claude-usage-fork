"""Tests for the fork's weekly even-pace target and days/hours reset label."""
from datetime import datetime

import pytest

pytest.importorskip("PySide6")

from claude_usage.overlay import (  # noqa: E402
    WEEK_SECONDS,
    _format_days_hours,
    _format_reset_short,
    _weekly_target,
)


def _in(seconds: float) -> int:
    return int(datetime.now().timestamp() + seconds)


@pytest.mark.parametrize("days_left, expected", [(6, 1 / 7), (3, 4 / 7), (0.5, 6.5 / 7)])
def test_weekly_target_is_elapsed_fraction(days_left, expected):
    assert _weekly_target(_in(days_left * 86400)) == pytest.approx(expected, abs=0.005)


def test_weekly_target_none_without_reset():
    assert _weekly_target(0) is None


def test_weekly_target_is_clamped():
    assert _weekly_target(_in(30 * 86400)) == 0.0          # reset further away than a week
    assert _weekly_target(_in(-3600)) == 1.0               # reset already passed


def test_week_is_seven_days():
    assert WEEK_SECONDS == 7 * 86400


@pytest.mark.parametrize("seconds, expected", [
    (3 * 86400 + 5 * 3600 + 60, "3 days, 5 hrs"),
    (86400 + 3700, "1 day, 1 hr"),
    (2 * 86400 + 30, "2 days, 0 hrs"),
])
def test_format_days_hours(seconds, expected):
    assert _format_days_hours(seconds) == expected


def test_reset_short_appends_remaining_when_over_a_day():
    label = _format_reset_short(_in(3 * 86400 + 5 * 3600 + 120))
    assert label.endswith("(3 days, 5 hrs)")


def test_reset_short_unchanged_under_a_day():
    assert _format_reset_short(_in(2 * 3600 + 600)).endswith("m")
    assert "(" not in _format_reset_short(_in(2 * 3600 + 600))
