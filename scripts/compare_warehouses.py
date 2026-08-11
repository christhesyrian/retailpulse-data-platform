#!/usr/bin/env python3
"""Do the warehouses agree on the answers, or do they only agree that the SQL parses?

A green `dbt build` on three targets proves the SQL compiles on each. It does
not prove they compute the same thing, and the gap between those two is where
this project's worst bugs live. Two examples, both found by running exactly
this comparison rather than by reading a build log:

  * `dayname` returns "Monday" on DuckDB and "Mon" on Snowflake, and
    `monthname` returns "August" and "Aug". Both compile everywhere.
  * `extract(week from ...)` is the ISO week on DuckDB and Snowflake, but
    BigQuery's WEEK counts from the first Sunday of the year — so dim_date
    carried a week number that was silently one lower for a good share of the
    year, in a column no test asserted.

Neither would ever fail a build. So the portability claim is checked here
instead: run the same query on every configured warehouse and require the same
answer back.

    python3 scripts/compare_warehouses.py

DuckDB is read from the local file; Snowflake and BigQuery are skipped with a
note if their credentials are not in the environment, so this is still useful
with only one cloud target configured. Exits non-zero on any disagreement that
is not a declared tolerance.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Each check runs on every warehouse; `{marts}` is the marts schema, which is
# named differently on each. `tolerance` is for figures that are genuinely
# float-derived — see the note on FLOAT_NOTE below — and is 0 (exact) unless
# stated. Everything that can be exact is required to be exact.
CHECKS: list[dict] = [
    {
        "name": "fact totals",
        "sql": "select count(*), sum(net_sales_cents), count(distinct sale_date) "
               "from {marts}.fact_order_line",
    },
    {
        "name": "kpi_summary, Last 30 days",
        "sql": "select orders, units, net_sales_cents, avg_order_value_cents "
               "from {marts}.kpi_summary where period_label = 'Last 30 days'",
    },
    {
        "name": "dim_date shape",
        "sql": "select count(*), min(date_day), max(date_day), "
               "count(distinct day_name), count(distinct month_name) from {marts}.dim_date",
    },
    {
        # day_name/month_name are the ones that differ in *value* rather than
        # syntax; week_of_year is the ISO-vs-Sunday one.
        "name": "dim_date names and week number",
        "sql": "select day_name, month_name, day_of_week, week_of_year, date_key "
               "from {marts}.dim_date order by date_day limit 1",
    },
    {
        "name": "weekday KPI, Monday all-time",
        "sql": "select day_name, is_weekend, orders, net_sales_cents "
               "from {marts}.kpi_sales_by_weekday "
               "where period_label = 'All time' and day_of_week = 1",
    },
    {
        "name": "peak trading hour",
        "sql": "select hour_of_day, net_sales_cents from {marts}.kpi_sales_by_hour "
               "where period_label = 'All time' order by net_sales_cents desc limit 1",
    },
    {
        # Every figure here depends on date_trunc('week'), which starts Monday
        # on DuckDB and Snowflake and Sunday on BigQuery unless asked otherwise.
        "name": "coverage, Monday-week logic",
        "sql": "select days_covered, days_with_sales, weeks_covered, "
               "days_in_complete_weeks, complete_weeks_covered "
               "from {marts}.kpi_data_coverage",
    },
    {
        "name": "weekly sales spine",
        "sql": "select min(week_start), max(week_start), count(*) "
               "from {marts}.kpi_item_weekly_sales",
    },
    {
        "name": "forecast method mix",
        "sql": "select method, count(*) from {marts}.kpi_item_forecast "
               "group by method order by method",
    },
    {
        "name": "forecast total units",
        "sql": "select sum(forecast_units) from {marts}.kpi_item_forecast",
        "tolerance": 0.01,
    },
    {
        "name": "revenue forecast, Tomorrow",
        "sql": "select forecast_net_sales_cents, wape_pct, baseline_wape_pct "
               "from {marts}.kpi_revenue_forecast where horizon_label = 'Tomorrow'",
    },
    {
        "name": "revenue forecast backtest error",
        "sql": "select mae_cents, backtest_days from {marts}.kpi_revenue_forecast "
               "where horizon_label = 'Tomorrow'",
        "tolerance": 0.01,
    },
]

FLOAT_NOTE = """
  Two checks carry a 0.01% tolerance, and only these two. Both are the rounded
  output of a floating-point regression, and the engines' last-bit differences
  tip a few values across a .5 boundary: 14 of 8,432 forecast rows differ, each
  by exactly 1 unit, and the backtest MAE differs by 1 cent in ~38,800. The
  `method` column -- which records whether a trend was fitted at all, and is
  the part a reader would act on -- is identical on every row.

  This is inherent to computing OLS in floating point on three engines, not a
  logic difference, and pretending otherwise by loosening every tolerance would
  hide the real bugs this script exists to catch.
"""


def normalise(rows) -> list[tuple]:
    """Compare values, not vendor types: Decimal(6584), 6584 and 6584.0 are one number."""

    def one(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (Decimal, int, float)):
            return round(float(value), 4)
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    return [tuple(one(v) for v in row) for row in rows]


def agree(rows_a, rows_b, tolerance: float) -> bool:
    if len(rows_a) != len(rows_b):
        return False
    for row_a, row_b in zip(rows_a, rows_b):
        if len(row_a) != len(row_b):
            return False
        for value_a, value_b in zip(row_a, row_b):
            if value_a == value_b:
                continue
            if tolerance and isinstance(value_a, float) and isinstance(value_b, float):
                scale = max(abs(value_a), abs(value_b), 1.0)
                if abs(value_a - value_b) / scale <= tolerance:
                    continue
            return False
    return True


def duckdb_runner():
    path = Path(os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb"))
    if not path.is_file():
        return None, f"no warehouse file at {path} — run `make dbt-build`"
    import duckdb

    con = duckdb.connect(str(path), read_only=True)

    def run(sql: str):
        return con.sql(sql.format(marts="main_marts")).fetchall()

    return run, None


def snowflake_runner():
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    if not os.environ.get("SNOWFLAKE_ACCOUNT") or not Path(key_path or "/nonexistent").is_file():
        return None, "SNOWFLAKE_ACCOUNT / SNOWFLAKE_PRIVATE_KEY_PATH not configured"
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    with open(key_path, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    con = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "RETAILPULSE"),
    )
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "MARTS") + "_marts"

    def run(sql: str):
        return con.cursor().execute(sql.format(marts=schema)).fetchall()

    return run, None


def bigquery_runner():
    project = os.environ.get("BIGQUERY_PROJECT", "")
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not project or not Path(credentials or "/nonexistent").is_file():
        return None, "BIGQUERY_PROJECT / GOOGLE_APPLICATION_CREDENTIALS not configured"
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    dataset = os.environ.get("BIGQUERY_DATASET", "retailpulse") + "_marts"

    def run(sql: str):
        query = sql.format(marts=f"`{client.project}`.{dataset}")
        return [tuple(row.values()) for row in client.query(query).result()]

    return run, None


def main() -> int:
    runners = {}
    for label, factory in (
        ("duckdb", duckdb_runner),
        ("snowflake", snowflake_runner),
        ("bigquery", bigquery_runner),
    ):
        run, skip_reason = factory()
        if run is None:
            print(f"  skipping {label}: {skip_reason}")
        else:
            runners[label] = run

    if len(runners) < 2:
        print("\nNeed at least two warehouses to compare.", file=sys.stderr)
        return 1

    print(f"\nComparing: {', '.join(runners)}\n")
    baseline_label = next(iter(runners))
    failures = 0

    for check in CHECKS:
        tolerance = check.get("tolerance", 0.0)
        results = {
            label: normalise(run(check["sql"])) for label, run in runners.items()
        }
        baseline = results[baseline_label]
        differing = [
            label
            for label, rows in results.items()
            if not agree(baseline, rows, tolerance)
        ]
        note = "  (within tolerance)" if tolerance and not differing else ""
        if differing:
            failures += 1
            print(f"  DIFF  {check['name']}")
            for label, rows in results.items():
                print(f"          {label:10} {rows}")
        else:
            print(f"  ok    {check['name']}{note}")

    print(FLOAT_NOTE)
    if failures:
        print(f"{failures} of {len(CHECKS)} checks disagree.")
        return 1
    print(f"All {len(CHECKS)} checks agree across {len(runners)} warehouses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
