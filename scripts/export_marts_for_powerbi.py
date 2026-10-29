#!/usr/bin/env python3
"""Export the Gold marts to Parquet so a Power BI model opens with no credentials.

Power BI connects to BigQuery natively, and that is what the published report
reads. This script exists for the other path — the one that matters to anyone
who clones the repo and has no cloud account:

    make demo-data        # synthetic warehouse, no Square token
    make powerbi-export   # marts -> powerbi/data/*.parquet

Same purpose as the DuckDB `dev` target in the dbt profile. The point of this
project is that the warehouse is reachable without an account, and a semantic
layer that can only be opened by someone holding a service-account key would
quietly give that up.

Parquet rather than CSV because the types are the whole value. Money is BIGINT
cents, `sale_date` is a DATE, and `has_cost` is a BOOLEAN — all of which CSV
flattens to text for Power Query to re-infer, one column at a time, differently
than dbt did. `--format csv` is available for tools that cannot read Parquet,
and is not the default for that reason.

Money stays in integer cents. Converting here would put a presentation decision
in the export, where neither the dbt tests nor the DAX measures can see it; the
measures divide by 100 exactly once instead. See docs/powerbi.md.

Usage:
    python3 scripts/export_marts_for_powerbi.py
    python3 scripts/export_marts_for_powerbi.py --include-kpi
    python3 scripts/export_marts_for_powerbi.py --format csv --out /tmp/marts
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Explicit lists rather than a `dim_*`/`fact_*` glob, for the same reason
# load_silver_to_bigquery.py names its tables: an export is an interface, and a
# glob silently widens it the next time a model is added.
DIMENSIONS = [
    "dim_date",
    "dim_item",
    "dim_location",
    "dim_vendor",
    "dim_period",
    # Exported for point-in-time work, but deliberately NOT related to the facts
    # in the base model — one item resolves to several history rows, so a
    # relationship on item_key fans out. `assert_item_history_has_no_fanout`
    # guards the SQL side of the same trap.
    "dim_item_history",
]

FACTS = [
    "fact_order_line",
    "fact_payment",
    "fact_order_line_margin",
    "fact_inventory_snapshot",
]

# Only needed by the reconciliation harness, which checks each DAX measure
# against its SQL counterpart. Left out of the default export because the whole
# argument of docs/powerbi.md is that the semantic model rebuilds these in DAX
# rather than importing the answers.
KPI_TABLES = [
    "kpi_summary",
    "kpi_daily_sales",
    "kpi_data_coverage",
    "kpi_sales_by_category",
    "kpi_sales_by_weekday",
    "kpi_sales_by_hour",
    "kpi_payment_methods",
    "kpi_item_sales",
    "kpi_margin_by_category",
    "kpi_margin_by_vendor",
    "kpi_inventory_position",
    "rpt_order_payment_reconciliation",
]

# Types Power BI's Parquet reader either rejects or silently mangles. None of
# these are produced by the current models; the check is here because the
# failure mode is a column that arrives empty rather than a load that fails.
UNSUPPORTED_TYPES = ("HUGEINT", "UHUGEINT", "INTERVAL", "UNION", "BIT")


def find_marts_schema(con) -> str:
    """Locate the marts schema rather than hard-coding it.

    dbt composes the name from the target's own schema plus the `+schema: marts`
    suffix, so it is `main_marts` on the local DuckDB target and something else
    anywhere the profile's schema differs. Hard-coding it means this script
    breaks the first time someone runs it against a warehouse built by a
    different target, with a "table does not exist" error that points at the
    table instead of the schema.
    """
    rows = con.execute(
        """
        select distinct table_schema
        from information_schema.tables
        where table_schema like '%marts'
        order by length(table_schema), table_schema
        """
    ).fetchall()
    if not rows:
        raise SystemExit(
            "No marts schema found in the warehouse. Run `make dbt-build` "
            "(or `make demo-data` for a synthetic warehouse) first."
        )
    return rows[0][0]


def problem_columns(con, schema: str, table: str) -> list[str]:
    rows = con.execute(
        """
        select column_name, data_type
        from information_schema.columns
        where table_schema = ? and table_name = ?
        """,
        [schema, table],
    ).fetchall()
    return [
        f"{name} ({dtype})"
        for name, dtype in rows
        if any(bad in str(dtype).upper() for bad in UNSUPPORTED_TYPES)
    ]


def looks_synthetic(con, schema: str) -> bool:
    """Is this the synthetic warehouse, or the real store's?

    Worth answering out loud. The export is described — accurately — as the path
    that needs no credentials, which makes it easy to assume the files it writes
    are the demo fixture and therefore safe to attach to something. Run against
    the production warehouse it writes real sales history instead, and the files
    look identical. `generate_synthetic_bronze.py` stamps this location id, so
    it is the one durable marker that survives into Gold.
    """
    row = con.execute(
        f'SELECT count(*) FROM "{schema}"."dim_location" '
        "WHERE square_location_id LIKE 'loc-synthetic%'"
    ).fetchone()
    return bool(row and row[0])


def export(con, schema: str, table: str, out_dir: Path, fmt: str) -> int:
    suffix = "parquet" if fmt == "parquet" else "csv"
    path = out_dir / f"{table}.{suffix}"
    copy_options = "(FORMAT PARQUET)" if fmt == "parquet" else "(FORMAT CSV, HEADER)"
    con.execute(f'COPY (SELECT * FROM "{schema}"."{table}") TO \'{path}\' {copy_options}')
    return con.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "powerbi" / "data",
        help="Directory to write into (default: powerbi/data).")
    parser.add_argument(
        "--format", choices=("parquet", "csv"), default="parquet",
        help="Output format (default: parquet, which preserves types).")
    parser.add_argument(
        "--include-kpi", action="store_true",
        help="Also export the kpi_* models, which the DAX reconciliation "
             "harness compares against. Not part of the semantic model.")
    parser.add_argument(
        "--warehouse", type=Path,
        default=Path(os.environ.get("RETAILPULSE_WAREHOUSE_PATH",
                                    ROOT / "data" / "gold" / "warehouse.duckdb")),
        help="DuckDB warehouse to read (default: $RETAILPULSE_WAREHOUSE_PATH).")
    args = parser.parse_args()

    if not args.warehouse.is_file():
        print(f"No warehouse at {args.warehouse}", file=sys.stderr)
        print("Run `make demo-data` to build one from synthetic data, or "
              "`make dbt-build` if Silver already exists.", file=sys.stderr)
        return 1

    try:
        import duckdb
    except ImportError:
        print("duckdb is not installed — run `make install`.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.warehouse), read_only=True)
    schema = find_marts_schema(con)

    tables = DIMENSIONS + FACTS + (KPI_TABLES if args.include_kpi else [])
    present = {
        row[0] for row in con.execute(
            "select table_name from information_schema.tables where table_schema = ?",
            [schema],
        ).fetchall()
    }

    missing = [t for t in tables if t not in present]
    if missing:
        print(f"Missing from {schema}: {', '.join(missing)}", file=sys.stderr)
        print("The warehouse is built but incomplete — run `make dbt-build`.", file=sys.stderr)
        return 1

    print(f"Reading {schema} from {args.warehouse}")
    print(f"Writing {args.format} to {args.out}\n")

    empty, flagged, total = [], [], 0
    for table in tables:
        bad = problem_columns(con, schema, table)
        rows = export(con, schema, table, args.out, args.format)
        total += rows
        note = ""
        if rows == 0:
            empty.append(table)
            note = "  <- empty"
        if bad:
            flagged.append((table, bad))
            note += f"  <- unsupported: {', '.join(bad)}"
        print(f"  {table:<34} {rows:>9,} rows{note}")

    print(f"\n{len(tables)} tables, {total:,} rows.")

    # An empty dimension is not an error — dim_vendor is empty whenever no
    # vendor costs have been supplied, which is the normal state of a clone.
    # It IS worth saying out loud, because in Power BI the symptom is a visual
    # that renders blank rather than one that complains.
    synthetic = looks_synthetic(con, schema)

    if empty:
        print(f"\nEmpty ({', '.join(empty)}) — any visual built on these will "
              f"render blank.")
        if "dim_vendor" in empty:
            print("  dim_vendor is empty because data/input/vendor_costs.csv has "
                  "no rows.")
            if synthetic:
                print("  Fill it with:")
                print("    python3 scripts/generate_synthetic_vendor_costs.py")
                print("  then rebuild (`make dbt-build`) and export again.")
            else:
                # Deliberately NOT recommending the generator here. Invented
                # costs against real revenue produce a margin that is fiction
                # presented as a fact — wrong on a dashboard, worse for a buying
                # decision. Real costs or no margin visuals. See docs/powerbi.md.
                print("  This warehouse holds real sales, so the synthetic cost")
                print("  generator is the wrong fix: invented costs against real")
                print("  revenue produce a margin figure that reads as fact.")
                print("  Supply real unit costs in that CSV, or leave the margin")
                print("  visuals out. See docs/powerbi.md.")

    if flagged:
        print("\nColumns Power BI may not read correctly:", file=sys.stderr)
        for table, bad in flagged:
            print(f"  {table}: {', '.join(bad)}", file=sys.stderr)
        return 1

    if synthetic:
        print("\nSource: synthetic warehouse. These files are safe to publish.")
    else:
        print(f"\nSource: PRODUCTION data — {total:,} rows of real store history.")
        print(f"  {args.out} is git-ignored and must stay that way. A report built")
        print("  on these files stays private: no `Publish to web`, no share link.")
        print("  For a publishable report, build the synthetic warehouse instead:")
        print("    make demo-data && make powerbi-export")

    print("\nNext: point a Power BI semantic model at this directory, or connect "
          "to BigQuery directly. See docs/powerbi.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
