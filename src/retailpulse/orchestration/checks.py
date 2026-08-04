"""Asset checks: the questions dbt tests cannot ask.

dbt's tests run *inside* a build and assert things about rows — uniqueness,
nullability, referential integrity. These two checks assert things about the
warehouse as an operational system, which is a different question and is why
they live here rather than as more `dbt test` cases.

Both are deliberately cheap read-only queries, so attaching them to the schedule
costs nothing.
"""

# No `from __future__ import annotations` here — see the note in assets.py.

import os
from datetime import date

import duckdb
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    MetadataValue,
    asset_check,
)
from dagster_dbt import get_asset_key_for_model

from retailpulse.orchestration.dbt import retailpulse_dbt_assets
from retailpulse.orchestration.resources import paths

# How stale the newest sale may be before the pipeline is considered broken.
# Two days rather than one because a single missed nightly run is a hiccup; two
# in a row means extraction has actually stopped.
MAX_SALE_AGE_DAYS = int(os.environ.get("RETAILPULSE_MAX_SALE_AGE_DAYS", "2"))

# The store trades from roughly 08:00 to midnight. Any peak outside that band
# means timestamps are being read in the wrong timezone (see below).
BUSINESS_HOURS = range(8, 24)


def _query(sql: str):
    """Read-only query against the warehouse file.

    read_only matters: the Streamlit dashboard may hold the same DuckDB file
    open, and a writable connection would fail to acquire the lock.
    """
    if not paths.warehouse.exists():
        return None
    with duckdb.connect(str(paths.warehouse), read_only=True) as connection:
        return connection.sql(sql).fetchone()


@asset_check(
    asset=get_asset_key_for_model([retailpulse_dbt_assets], "fact_order_line"),
    name="sales_are_fresh",
    description="Fail when the newest sale in the warehouse is more than two days old.",
    blocking=False,
)
def sales_are_fresh() -> AssetCheckResult:
    """Catch the silent failure mode: a green pipeline that stopped ingesting.

    Every model can build successfully, every dbt test can pass, and the
    dashboard can look perfectly healthy while showing data that stopped
    updating a week ago — because "no new rows" breaks no constraint. This is
    the check that turns that into a visible failure.
    """
    row = _query("select max(sale_date) from main_marts.fact_order_line")
    if row is None:
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.ERROR,
            description=f"Warehouse not found at {paths.warehouse}.",
        )

    latest_sale = row[0]
    if latest_sale is None:
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.ERROR,
            description="fact_order_line contains no dated sales.",
        )

    age_days = (date.today() - latest_sale).days
    return AssetCheckResult(
        passed=age_days <= MAX_SALE_AGE_DAYS,
        severity=AssetCheckSeverity.ERROR,
        description=(
            f"Newest sale is {latest_sale} ({age_days} days old); "
            f"threshold is {MAX_SALE_AGE_DAYS} days."
        ),
        metadata={
            "latest_sale_date": MetadataValue.text(str(latest_sale)),
            "age_days": age_days,
            "threshold_days": MAX_SALE_AGE_DAYS,
        },
    )


@asset_check(
    asset=get_asset_key_for_model([retailpulse_dbt_assets], "kpi_sales_by_hour"),
    name="peak_hour_is_within_business_hours",
    description="Guard against timezone regressions by asserting the busiest hour is plausible.",
    blocking=False,
)
def peak_hour_is_within_business_hours() -> AssetCheckResult:
    """Encode the most expensive bug this project has had, as a test.

    Sales were attributed to the UTC calendar day for a year. Every total still
    reconciled, every test still passed, and nothing looked wrong — the bug was
    found by noticing the hour-of-day chart peaked at 2am. A liquor store does
    not do its best trade at 2am; that peak was the evening trade shifted by the
    UTC offset.

    So: assert the shape, not just the totals. If a future change re-derives an
    hour from a UTC timestamp, the peak moves out of business hours and this
    fails immediately instead of a year later.
    """
    row = _query(
        """
        select hour_of_day, net_sales_cents
        from main_marts.kpi_sales_by_hour
        where period_label = 'All time'
        order by net_sales_cents desc
        limit 1
        """
    )
    if row is None or row[0] is None:
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.WARN,
            description="No hourly sales available to check.",
        )

    peak_hour, net_sales_cents = row
    return AssetCheckResult(
        passed=peak_hour in BUSINESS_HOURS,
        severity=AssetCheckSeverity.ERROR,
        description=(
            f"Busiest hour is {peak_hour:02d}:00, expected within "
            f"{BUSINESS_HOURS.start:02d}:00-{BUSINESS_HOURS.stop - 1:02d}:00. "
            "A peak outside these hours means timestamps are being read as UTC."
        ),
        metadata={
            "peak_hour": peak_hour,
            "peak_hour_net_sales": MetadataValue.float(round(net_sales_cents / 100, 2)),
        },
    )
