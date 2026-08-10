#!/usr/bin/env python3
"""Smoke-test the KPI tables the dashboard depends on.

CI doesn't install Streamlit (it's a presentation-only dependency), so
this guards the dashboard-to-warehouse contract without it: it selects
from every KPI table dashboard/app.py reads and fails if any is missing
or (where it must not be) empty. Catches a renamed model or column
before it reaches the dashboard.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path(
    os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
)

# The parameterized macros the dashboard calls for every window, preset or not.
# dbt's assert_range_macros_match_periods already proves they agree with the
# precomputed models; what this checks is cruder and complementary — that they
# exist, are callable with two dates, and return something. A macro that failed
# to register would leave `dbt build` green (the test would simply find nothing
# to disagree with) and break every page load.
RANGE_MACROS = [
    ("rp_summary_range", True),
    ("rp_category_range", True),
    ("rp_weekday_range", True),
    ("rp_hour_range", True),
    ("rp_payments_range", True),
    ("rp_items_range", True),
]


def check_range_macros(con) -> list[str]:
    """Call every range macro over an arbitrary window that matches no preset."""
    failures: list[str] = []
    bounds = con.execute(
        "select min(sale_date), max(sale_date) from main_marts.fact_order_line "
        "where sale_date is not null"
    ).fetchone()
    if not bounds or bounds[0] is None:
        return ["no dated sales, cannot exercise the range macros"]

    # Deliberately not a preset length: presets are covered by the dbt test, so
    # this exercises the arbitrary-window path the dashboard now depends on.
    end = bounds[1]
    start = max(bounds[0], end - timedelta(days=39))

    for macro, must_have_rows in RANGE_MACROS:
        try:
            rows = con.execute(f"select count(*) from {macro}(?, ?)", (start, end)).fetchone()[0]
        except duckdb.Error as exc:
            failures.append(f"{macro}: not callable ({str(exc).splitlines()[0]})")
            continue
        if must_have_rows and rows == 0:
            failures.append(f"{macro}: returned no rows for {start}..{end}")
        else:
            print(f"  ok: {macro}({start}, {end}) -> {rows:,} row(s)")

    return failures


# (table, must_be_non_empty) — every relation dashboard/app.py queries.
# kpi_inventory_position is deliberately absent: the model still builds and is
# tested, but the dashboard no longer shows inventory.
EXPECTED = [
    ("main_marts.dim_period", True),
    ("main_marts.kpi_summary", True),
    ("main_marts.kpi_data_coverage", True),
    ("main_marts.kpi_daily_sales", True),
    ("main_marts.kpi_sales_by_category", True),
    ("main_marts.kpi_sales_by_weekday", True),
    ("main_marts.kpi_sales_by_hour", True),
    ("main_marts.kpi_payment_methods", True),
    ("main_marts.rpt_order_payment_reconciliation", False),
    ("main_marts.kpi_margin_by_category", False),
    ("main_marts.kpi_item_sales", True),
    ("main_marts.kpi_item_weekly_sales", True),
    ("main_marts.kpi_item_forecast", False),
]

# The dashboard picks rows by period_label, so every period-aware model must
# carry a row for each period the picker offers. A model missing a period
# silently renders an empty tab, which no row-count check would catch.
PERIOD_AWARE = [
    "main_marts.kpi_summary",
    "main_marts.kpi_sales_by_category",
    "main_marts.kpi_sales_by_weekday",
    "main_marts.kpi_sales_by_hour",
    "main_marts.kpi_payment_methods",
    "main_marts.kpi_item_sales",
]


def main() -> int:
    if not WAREHOUSE_PATH.exists():
        print(f"Warehouse not found at {WAREHOUSE_PATH}. Run the pipeline first.")
        return 1

    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    failures = []
    try:
        for table, must_be_non_empty in EXPECTED:
            try:
                count = con.execute(f"select count(*) from {table}").fetchone()[0]
            except duckdb.Error as exc:
                failures.append(f"{table}: query failed ({exc})")
                continue
            if must_be_non_empty and count == 0:
                failures.append(f"{table}: expected rows but found 0")
            else:
                print(f"  ok: {table} ({count} rows)")

        # kpi_data_coverage must be exactly one row — the dashboard reads
        # .iloc[0] on it.
        rows = con.execute("select count(*) from main_marts.kpi_data_coverage").fetchone()[0]
        if rows != 1:
            failures.append(f"kpi_data_coverage must have exactly 1 row, found {rows}")

        # Every period offered by the picker must exist in every period-aware
        # model, and kpi_summary must hold exactly one row per period since the
        # dashboard reads .iloc[0] after filtering to one.
        expected_periods = {
            row[0]
            for row in con.execute("select period_label from main_marts.dim_period").fetchall()
        }
        for table in PERIOD_AWARE:
            try:
                found = {
                    row[0]
                    for row in con.execute(f"select distinct period_label from {table}").fetchall()
                }
            except duckdb.Error as exc:
                failures.append(f"{table}: period_label query failed ({exc})")
                continue
            missing = expected_periods - found
            if missing:
                failures.append(f"{table}: no rows for period(s) {sorted(missing)}")
            else:
                print(f"  ok: {table} covers all {len(expected_periods)} periods")

        summary_dupes = con.execute(
            "select count(*) from (select period_label from main_marts.kpi_summary "
            "group by 1 having count(*) > 1)"
        ).fetchone()[0]
        if summary_dupes:
            failures.append(f"kpi_summary has {summary_dupes} period(s) with more than one row")

        failures.extend(check_range_macros(con))
    finally:
        con.close()

    if failures:
        print("\nDASHBOARD QUERY SMOKE TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nDashboard query smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
