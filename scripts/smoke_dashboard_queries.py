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
from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path(
    os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
)

# (table, must_be_non_empty) — every relation dashboard/app.py queries.
EXPECTED = [
    ("main_marts.kpi_summary", True),
    ("main_marts.kpi_daily_sales", True),
    ("main_marts.kpi_sales_by_category", True),
    ("main_marts.kpi_sales_by_weekday", True),
    ("main_marts.kpi_sales_by_hour", True),
    ("main_marts.kpi_payment_methods", True),
    ("main_marts.rpt_order_payment_reconciliation", False),
    ("main_marts.kpi_margin_by_category", False),
    ("main_marts.kpi_inventory_position", False),
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

        # kpi_summary must be exactly one row (dashboard reads .iloc[0]).
        summary_rows = con.execute("select count(*) from main_marts.kpi_summary").fetchone()[0]
        if summary_rows != 1:
            failures.append(f"kpi_summary must have exactly 1 row, found {summary_rows}")
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
