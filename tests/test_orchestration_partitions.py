"""Tests for the store-local day -> UTC window conversion.

This is the arithmetic a backfill depends on. If it is wrong, every partition
is quietly off by an offset and the resulting Bronze looks perfectly healthy —
which is exactly how the original UTC-date bug survived for a year. So the
DST boundaries are tested explicitly rather than assumed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from retailpulse.orchestration.partitions import partition_utc_window

LA = "America/Los_Angeles"


def _parse(value: str) -> datetime:
    assert value.endswith("Z"), f"Square requires a literal Z suffix, got {value!r}"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_standard_winter_day_is_utc_minus_8():
    begin, end = partition_utc_window("2026-01-15", LA)

    # PST is UTC-8, so the store's midnight is 08:00 UTC the same day.
    assert begin == "2026-01-15T08:00:00Z"
    assert end == "2026-01-16T08:00:00Z"


def test_standard_summer_day_is_utc_minus_7():
    begin, end = partition_utc_window("2026-07-15", LA)

    # PDT is UTC-7.
    assert begin == "2026-07-15T07:00:00Z"
    assert end == "2026-07-16T07:00:00Z"


def test_spring_forward_day_is_23_hours():
    """The day DST starts has only 23 hours; a fixed 24h window would overrun."""
    begin, end = partition_utc_window("2026-03-08", LA)

    assert _parse(end) - _parse(begin) == timedelta(hours=23)


def test_fall_back_day_is_25_hours():
    """The day DST ends has 25 hours; a fixed 24h window would drop an hour of trade."""
    begin, end = partition_utc_window("2026-11-01", LA)

    assert _parse(end) - _parse(begin) == timedelta(hours=25)


@pytest.mark.parametrize("start_day", ["2026-03-07", "2026-10-31", "2026-06-01"])
def test_consecutive_partitions_tile_without_gap_or_overlap(start_day):
    """Each day must end exactly where the next begins, including across DST.

    A gap loses sales; an overlap double-counts them. Both would reconcile
    against nothing and show up only as a wrong total much later.
    """
    day_one = datetime.fromisoformat(start_day).date()
    day_two = day_one + timedelta(days=1)

    _, first_end = partition_utc_window(day_one.isoformat(), LA)
    second_begin, _ = partition_utc_window(day_two.isoformat(), LA)

    assert first_end == second_begin


def test_window_is_a_utc_instant_not_a_local_wall_clock():
    """Guard the actual bug: the window must be tz-aware UTC, not a naive local time."""
    begin, _ = partition_utc_window("2026-07-15", LA)
    parsed = _parse(begin)

    assert parsed.tzinfo == timezone.utc
    # A naive "2026-07-15T00:00:00" would make this hour 0. The whole point is
    # that the store's midnight is *not* midnight UTC.
    assert parsed.hour == 7


def test_a_different_store_timezone_shifts_the_window():
    """The timezone is a parameter, not a hard-coded assumption about one merchant."""
    begin_la, _ = partition_utc_window("2026-07-15", LA)
    begin_ny, _ = partition_utc_window("2026-07-15", "America/New_York")

    assert begin_la == "2026-07-15T07:00:00Z"
    assert begin_ny == "2026-07-15T04:00:00Z"
