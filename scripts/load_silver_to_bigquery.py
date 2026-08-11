#!/usr/bin/env python3
"""Load the Silver layer into BigQuery so the Gold models can build there.

The counterpart to load_silver_to_snowflake.py, and it exists for exactly the
same reason: **the dbt models are portable, the ingestion is not.**

On DuckDB, `silver.order_lines` is a source whose `external_location` points at
a Parquet file and dbt reads it straight off local disk. `external_location` is
a dbt-duckdb feature; every other adapter ignores it and resolves the same
source to an ordinary relation — here `<project>.silver.order_lines` — which
nothing has created. The build fails with "Dataset <project>:silver was not
found", which reads like a permissions problem and is actually an
architectural one.

BigQuery makes this easier than Snowflake did in one respect and harder in
another:

  * Easier — no casing problem. Snowflake folds unquoted identifiers to
    uppercase, so DuckDB's lowercase Parquet columns had to be recreated as
    uppercase DDL or every staging model failed on a column plainly visible in
    the table. BigQuery preserves the Parquet names as written, so the same
    unmodified model SQL resolves.

  * Harder — datasets are location-bound. A dataset created in the multi-region
    US cannot be queried from a job in us-central1 and vice versa, and the
    error ("Not found: Dataset ... was not found in location US") names the
    missing dataset rather than the mismatch. So the datasets are created in
    BIGQUERY_LOCATION, the same variable the dbt profile uses.

Run it after `make silver`, then build with the prod target:

    python3 scripts/load_silver_to_bigquery.py
    RETAILPULSE_DBT_TARGET=prod dbt build --project-dir dbt --profiles-dir dbt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Silver tables are Parquet; the operator-maintained reference inputs are CSV.
# Dataset names match the `source` names in models/staging/_sources.yml.
SILVER_TABLES = [
    "locations",
    "catalog_items",
    "order_lines",
    "payments",
    "inventory_snapshots",
]
REFERENCE_TABLES = ["vendor_costs", "category_overrides"]


def connect():
    from google.cloud import bigquery

    project = os.environ.get("BIGQUERY_PROJECT", "")
    if not project:
        raise SystemExit("BIGQUERY_PROJECT is not set.")
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not credentials or not Path(credentials).is_file():
        raise SystemExit(
            "GOOGLE_APPLICATION_CREDENTIALS is not set or does not point at a "
            "service-account key file."
        )
    return bigquery.Client(project=project)


def ensure_dataset(client, dataset_id: str, location: str) -> None:
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery

    try:
        client.get_dataset(dataset_id)
    except NotFound:
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = location
        client.create_dataset(dataset)


def load_file(client, dataset: str, table: str, path: Path, file_type: str) -> int:
    """Load one file into a table, replacing whatever was there. Returns rows."""
    from google.cloud import bigquery

    table_id = f"{client.project}.{dataset}.{table}"

    if file_type == "PARQUET":
        # Types come from the file's own footer. Hand-maintaining DDL for five
        # tables across three warehouses is how the three silently drift apart.
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
    else:
        # The explicit route, for the same reason the Snowflake loader takes
        # it: these two files are optional operator inputs, and "header only,
        # no data rows" is their *normal* state — a store with no vendor costs
        # and no category renaming. BigQuery's autodetect infers nothing from a
        # file with no rows to sample and the load produces a table with no
        # columns, so the staging models then fail on every column.
        #
        # All-STRING matches what dbt-duckdb's auto_detect produces for the
        # same header-only file, so the staging models cast identically on
        # every warehouse rather than meeting different types.
        header = path.read_text(encoding="utf-8").splitlines()[0]
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=[
                bigquery.SchemaField(column.strip(), "STRING")
                for column in header.split(",")
            ],
        )

    with path.open("rb") as handle:
        client.load_table_from_file(handle, table_id, job_config=config).result()
    return client.get_table(table_id).num_rows


def main() -> int:
    silver_dir = Path(os.environ.get("RETAILPULSE_SILVER_DIR", "data/silver"))
    input_dir = Path(os.environ.get("RETAILPULSE_INPUT_DIR", "data/input"))
    location = os.environ.get("BIGQUERY_LOCATION", "US")

    targets: list[tuple[str, str, Path, str]] = []
    for name in SILVER_TABLES:
        targets.append(("silver", name, silver_dir / f"{name}.parquet", "PARQUET"))
    for name in REFERENCE_TABLES:
        targets.append(("reference", name, input_dir / f"{name}.csv", "CSV"))

    missing = [str(p) for _, _, p, _ in targets if not p.is_file()]
    if missing:
        print("Missing input files — run `make silver` first:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    client = connect()
    for dataset in ("silver", "reference"):
        ensure_dataset(client, f"{client.project}.{dataset}", location)

    for dataset, table, path, file_type in targets:
        rows = load_file(client, dataset, table, path, file_type)
        print(f"  loaded {dataset}.{table:<22} {rows:>9,} rows")

    print("\nSilver loaded into BigQuery.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
