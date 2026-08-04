"""Daily partitions, defined on the store's calendar rather than UTC.

The whole point of partitioning Bronze by day is that a partition should mean
something to the business: "Tuesday's trade". Square's API, though, takes a UTC
window. A liquor store in America/Los_Angeles does its heaviest trade in the
evening, which is already the next day in UTC — so a partition built on UTC
midnights silently splits an evening across two partitions and puts a Tuesday
night's sales in Wednesday's file.

This is the same bug, at a different layer, that `fact_order_line` already
fixes for reporting (see the `closed_at_local` / `sale_date` columns). It cost
twelve models to unpick there. Encoding it here means a backfill of
"2026-03-14" asks Square for the window that actually corresponds to the store's
March 14th, including the DST-shifted ones where that window is 23 or 25 hours
long rather than 24.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dagster import DailyPartitionsDefinition

# The operating store's timezone. Overridable so this isn't hard-coded to one
# merchant, but both of the real locations are America/Los_Angeles.
STORE_TIMEZONE = os.environ.get("RETAILPULSE_STORE_TIMEZONE", "America/Los_Angeles")

# The first day with sales in the warehouse. Partitions before this exist only
# as empty windows, so starting here keeps the backfill UI honest about what
# can actually be filled.
FIRST_PARTITION_DATE = os.environ.get("RETAILPULSE_FIRST_PARTITION_DATE", "2025-07-26")

daily_partitions = DailyPartitionsDefinition(
    start_date=FIRST_PARTITION_DATE,
    timezone=STORE_TIMEZONE,
)


def _as_square_timestamp(value: datetime) -> str:
    """Square wants RFC 3339 with a literal Z, not the +00:00 Python emits."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def partition_utc_window(
    partition_key: str, store_timezone: str = STORE_TIMEZONE
) -> tuple[str, str]:
    """Return the UTC [begin, end) window for one store-local calendar day.

    `partition_key` is a "YYYY-MM-DD" date in the store's timezone. The window
    is half-open: it starts at local midnight and ends at the next local
    midnight, so consecutive partitions tile the timeline without overlapping
    or dropping the hour that DST adds or removes.
    """

    day = date.fromisoformat(partition_key)
    tz = ZoneInfo(store_timezone)

    # Combining with tzinfo (rather than localising afterwards) lets zoneinfo
    # resolve the correct UTC offset for that specific date, which is what
    # makes the DST boundaries come out at 23h and 25h instead of always 24h.
    begin_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)

    return _as_square_timestamp(begin_local), _as_square_timestamp(end_local)
