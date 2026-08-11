#!/usr/bin/env python3
"""Load the Silver layer into Snowflake so the Gold models can build there.

This script exists because of a real boundary the multi-warehouse work exposed,
and it is worth stating plainly: **the dbt models are portable, the ingestion is
not.**

On DuckDB, `silver.order_lines` is a source whose `external_location` points at
a Parquet file, and dbt reads it straight off local disk. Snowflake has no
equivalent — it cannot read a file on a laptop — so the same source resolves to
`RETAILPULSE.SILVER.ORDER_LINES`, a table that nothing has created. The build
fails with "Schema 'RETAILPULSE.SILVER' does not exist", which looks like a
permissions problem and is actually an architectural one.

So the portability claim has to be precise. The 33 Gold models run unchanged on
either warehouse; what changes is how Silver arrives. That is normal — every
warehouse migration is mostly a loader rewrite — but it is the sort of thing
worth knowing before promising a move takes an afternoon.

The load itself is the standard Snowflake pattern:

    PUT           upload the Parquet/CSV to an internal stage
    INFER_SCHEMA  derive column types from the Parquet footer
    CREATE TABLE  USING TEMPLATE, so schemas are not hand-maintained
    COPY INTO     MATCH_BY_COLUMN_NAME, so column order does not matter

Run it after `make silver`, then build with the snowflake target:

    python3 scripts/load_silver_to_snowflake.py
    RETAILPULSE_DBT_TARGET=snowflake dbt build --project-dir dbt --profiles-dir dbt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Silver tables are Parquet; the operator-maintained reference inputs are CSV.
# Both become tables in Snowflake, in the schemas the dbt sources expect.
SILVER_TABLES = [
    "locations",
    "catalog_items",
    "order_lines",
    "payments",
    "inventory_snapshots",
]
REFERENCE_TABLES = ["vendor_costs", "category_overrides"]


def _private_key_bytes(path: Path) -> bytes:
    """Load the PKCS#8 private key in the DER form the connector wants."""
    from cryptography.hazmat.primitives import serialization

    with path.open("rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect():
    import snowflake.connector

    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    if not key_path or not Path(key_path).is_file():
        raise SystemExit(
            "SNOWFLAKE_PRIVATE_KEY_PATH is not set or does not point at a key file.\n"
            "Snowflake rejects password auth for programmatic connections on accounts "
            "with MFA enforced, so key-pair is the only route."
        )
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=_private_key_bytes(Path(key_path)),
        role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "RETAILPULSE"),
    )


def load_file(cur, schema: str, table: str, path: Path, file_type: str) -> int:
    """Stage one file and materialize it as a table. Returns the row count."""
    stage = f"{schema}.RP_STAGE"
    fmt = f"{schema}.RP_{file_type}_FORMAT"

    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    if file_type == "PARQUET":
        cur.execute(f"CREATE OR REPLACE FILE FORMAT {fmt} TYPE = PARQUET")
    else:
        cur.execute(
            f"CREATE OR REPLACE FILE FORMAT {fmt} TYPE = CSV "
            "SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '\"'"
        )
    cur.execute(f"CREATE STAGE IF NOT EXISTS {stage} FILE_FORMAT = {fmt}")

    # OVERWRITE so a re-run replaces the staged file rather than accumulating
    # copies that COPY INTO would then load twice.
    cur.execute(
        f"PUT 'file://{path.as_posix()}' @{stage} OVERWRITE = TRUE AUTO_COMPRESS = FALSE"
    )

    if file_type == "PARQUET":
        # Types come from the file's own footer — hand-maintaining DDL for five
        # tables across two warehouses is how the two silently drift apart — but
        # the DDL is assembled here rather than via CREATE TABLE ... USING
        # TEMPLATE, for one reason: casing.
        #
        # DuckDB writes Parquet with lowercase column names. USING TEMPLATE
        # reproduces them verbatim, which in Snowflake means quoted lowercase
        # identifiers ("order_id"). Snowflake folds unquoted identifiers in
        # ordinary SQL to uppercase, so every staging model then fails with
        # `invalid identifier 'ORDER_ID'` while the column is plainly visible in
        # the table. Creating the columns uppercase makes the same unmodified
        # model SQL resolve on both warehouses.
        inferred = cur.execute(
            f"SELECT COLUMN_NAME, TYPE FROM TABLE(INFER_SCHEMA("
            f"LOCATION => '@{stage}/{path.name}', FILE_FORMAT => '{fmt}'))"
        ).fetchall()
        if not inferred:
            raise RuntimeError(f"INFER_SCHEMA returned no columns for {path.name}")
        columns = ", ".join(f'"{name.upper()}" {dtype}' for name, dtype in inferred)
        cur.execute(f"CREATE OR REPLACE TABLE {schema}.{table} ({columns})")
        # CASE_INSENSITIVE is what bridges the Parquet's lowercase names to the
        # uppercase columns just created.
        cur.execute(
            f"COPY INTO {schema}.{table} FROM @{stage}/{path.name} "
            f"FILE_FORMAT = (FORMAT_NAME = '{fmt}') "
            "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE"
        )
    else:
        # CSV takes the explicit route, because INFER_SCHEMA cannot infer types
        # from a file with no data rows — and "no data rows" is the *normal*
        # state of these two. They are optional operator inputs: a store with no
        # vendor costs and no category renaming has header-only files, which is
        # precisely what "I have none of these" looks like to dbt. INFER_SCHEMA
        # returns nothing there and CREATE ... USING TEMPLATE fails with
        # "template must be a non-null JSON array", which reads like a bug in
        # the loader rather than an empty input.
        #
        # All-VARCHAR matches what dbt-duckdb's auto_detect produces for the
        # same header-only file, so the staging models cast identically on both
        # warehouses instead of meeting different types.
        header = path.read_text(encoding="utf-8").splitlines()[0]
        columns = ", ".join(f'"{c.strip().upper()}" VARCHAR' for c in header.split(","))
        cur.execute(f"CREATE OR REPLACE TABLE {schema}.{table} ({columns})")
        cur.execute(
            f"COPY INTO {schema}.{table} FROM @{stage}/{path.name} "
            f"FILE_FORMAT = (FORMAT_NAME = '{fmt}')"
        )
    return cur.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]


def main() -> int:
    silver_dir = Path(os.environ.get("RETAILPULSE_SILVER_DIR", "data/silver"))
    input_dir = Path(os.environ.get("RETAILPULSE_INPUT_DIR", "data/input"))

    targets: list[tuple[str, str, Path, str]] = []
    for name in SILVER_TABLES:
        targets.append(("SILVER", name.upper(), silver_dir / f"{name}.parquet", "PARQUET"))
    for name in REFERENCE_TABLES:
        targets.append(("REFERENCE", name.upper(), input_dir / f"{name}.csv", "CSV"))

    missing = [str(p) for _, _, p, _ in targets if not p.is_file()]
    if missing:
        print("Missing input files — run `make silver` first:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    connection = connect()
    try:
        cur = connection.cursor()
        for schema, table, path, file_type in targets:
            rows = load_file(cur, schema, table, path, file_type)
            print(f"  loaded {schema}.{table:<22} {rows:>9,} rows")
    finally:
        connection.close()

    print("\nSilver loaded into Snowflake.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
