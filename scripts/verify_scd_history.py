#!/usr/bin/env python3
"""Prove the Type 2 item dimension actually records history.

On a fresh warehouse every item has exactly one version, so
`assert_item_history_is_well_formed` and `assert_item_history_has_no_fanout`
both pass without demonstrating anything: a timeline of one point is trivially
contiguous, and a join that can only match one row cannot fan out. The tests
are worth having, but on their own they are not evidence.

This changes an item and checks the dimension noticed:

    1. build, and record the item's only version
    2. rename it and move it to another category, in Silver
    3. build again
    4. the old version must be closed off, not overwritten
    5. a new open version must carry the new values
    6. asking "what was this item as of <then>" must return the OLD values,
       which is the entire reason a Type 2 dimension exists
    7. dim_item must still show exactly one row for it — the current one

Runs against a generated fixture in a temp directory: no credentials, and it
never touches `data/`.

    python3 scripts/verify_scd_history.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
NEW_NAME = "Renamed By The History Check"
NEW_CATEGORY = "WINE"


def run_dbt(env: dict[str, str], *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt"),
         "--target", "dev", *args],
        capture_output=True, text=True, env=env, check=False,
    )
    if result.returncode != 0:
        print(result.stdout[-4000:])
        raise SystemExit("dbt build failed")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="retailpulse-scd-"))
    bronze, silver, inputs = workdir / "bronze", workdir / "silver", workdir / "input"
    warehouse = workdir / "warehouse.duckdb"

    env = os.environ.copy()
    env.update({
        "RETAILPULSE_SILVER_DIR": str(silver),
        "RETAILPULSE_INPUT_DIR": str(inputs),
        "RETAILPULSE_WAREHOUSE_PATH": str(warehouse),
        "RAW_DATA_DIR": str(bronze),
    })
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "scripts"))

    print("1. Building a warehouse and taking the first snapshot")
    import generate_synthetic_bronze as synth

    sys.argv = ["generate_synthetic_bronze", str(bronze), "--days", "60"]
    synth.main()

    from retailpulse.reference_inputs import ensure_reference_inputs
    from retailpulse.transform.silver import run_silver_transform

    run_silver_transform(bronze, silver)
    ensure_reference_inputs(input_dir=inputs, warehouse_path=warehouse)
    run_dbt(env)

    with duckdb.connect(str(warehouse), read_only=True) as con:
        target = con.sql("""
            select square_catalog_object_id, item_name, category_name, valid_from
            from main_marts.dim_item_history order by square_catalog_object_id limit 1
        """).fetchone()
        catalog_id, old_name, old_category, first_valid_from = target
        versions = con.sql(
            "select count(*) from main_marts.dim_item_history where square_catalog_object_id = ?",
            params=[catalog_id]).fetchone()[0]
    print(f"   {catalog_id}: {old_name!r} in {old_category!r}, {versions} version")

    print("2. Renaming it and moving it to another category, in Silver")
    catalog = silver / "catalog_items.parquet"
    with duckdb.connect() as con:
        cols = list(con.sql(
            f"select * from read_parquet('{catalog.as_posix()}') limit 0").columns)
        con.execute(f"""
            create table amended as
            select * exclude (item_name, category_name),
                   case when variation_id = ? then ? else item_name end as item_name,
                   case when variation_id = ? then ? else category_name end as category_name
            from read_parquet('{catalog.as_posix()}')
        """, [catalog_id, NEW_NAME, catalog_id, NEW_CATEGORY])
        con.execute(f"copy (select {', '.join(cols)} from amended) "
                    f"to '{catalog.as_posix()}' (format parquet)")

    print("3. Building again — the snapshot should notice")
    run_dbt(env)

    with duckdb.connect(str(warehouse), read_only=True) as con:
        rows = con.sql("""
            select version_number, item_name, category_name, valid_from, valid_to, is_current
            from main_marts.dim_item_history
            where square_catalog_object_id = ? order by version_number
        """, params=[catalog_id]).fetchall()

        print(f"4. History for {catalog_id}:")
        for v, name, cat, vf, vt, cur in rows:
            state = "current" if cur else "closed "
            print(f"     v{v} {state}  {str(name)[:34]:<36} {str(cat)[:10]:<12} "
                  f"{str(vf)[:19]} -> {str(vt)[:19] if vt else 'open'}")

        if len(rows) != 2:
            print(f"   FAIL: expected 2 versions, found {len(rows)}. The change "
                  f"was not recorded — the old row was overwritten.")
            return 1
        v1, v2 = rows
        if v1[5] or not v2[5]:
            print("   FAIL: the wrong version is marked current")
            return 1
        if v1[4] is None:
            print("   FAIL: the superseded version was never closed off")
            return 1
        if v2[1] != NEW_NAME or v2[2] != NEW_CATEGORY:
            print(f"   FAIL: the new version carries {v2[1]!r}/{v2[2]!r}")
            return 1
        if v1[1] != old_name or v1[2] != old_category:
            print(f"   FAIL: the old version was mutated to {v1[1]!r}/{v1[2]!r}")
            return 1
        print("   old version closed, new version open, neither overwritten")

        print("5. Asking what the item was as of the first snapshot")
        as_of = con.sql("""
            select item_name, category_name from main_marts.dim_item_history
            where square_catalog_object_id = ?
              and ? >= valid_from and (valid_to is null or ? < valid_to)
        """, params=[catalog_id, first_valid_from, first_valid_from]).fetchall()
        if len(as_of) != 1:
            print(f"   FAIL: a point-in-time lookup returned {len(as_of)} rows, expected 1")
            return 1
        if as_of[0] != (old_name, old_category):
            print(f"   FAIL: as-of lookup returned {as_of[0]}, expected the old values")
            return 1
        print(f"   {as_of[0][0]!r} in {as_of[0][1]!r} — the values it had then, "
              f"not the ones it has now")

        print("6. dim_item must still hold exactly one row for it")
        current = con.sql("""
            select item_name, category_name from main_marts.dim_item
            where square_catalog_object_id = ?
        """, params=[catalog_id]).fetchall()
        if len(current) != 1 or current[0] != (NEW_NAME, NEW_CATEGORY):
            print(f"   FAIL: dim_item has {len(current)} row(s): {current}")
            return 1
        print(f"   one row, showing the current {current[0][0]!r} in {current[0][1]!r}")

    shutil.rmtree(workdir, ignore_errors=True)
    print("\nType 2 history verified: changes are versioned rather than "
          "overwritten, and a point-in-time lookup returns the values in "
          "effect at that time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
