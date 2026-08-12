#!/usr/bin/env python3
"""Prove the incremental fact table agrees with a full rebuild — including
after a late-arriving edit.

`fact_order_line` is the only incremental model here, which buys speed and
costs the guarantee a full-refresh model gets for free: that the table matches
its source because it was just rebuilt from it. An incremental table can drift,
and the ways it drifts are quiet — a row missed outside the lookback window, a
stale row left behind, a duplicated key. None of them raise.

`assert_fact_matches_source` catches drift on every build. This script proves
the harder property, the one a reviewer will actually ask about:

    an amendment to an already-closed order is picked up

which is the case the obvious implementation gets wrong. Filtering on
`closed_at > max(closed_at)` never sees an edit to last Tuesday's order. This
runs that exact scenario end to end and fails if the edited value does not
reach the warehouse.

Everything happens in a temp directory against a generated fixture. It never
touches `data/`, needs no credentials, and does not care which warehouse you
normally build against — it uses DuckDB.

    python3 scripts/verify_incremental.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
# One line item's price is changed by this much, in cents. Distinctive enough
# that it cannot be confused with a real figure in the fixture.
PRICE_BUMP = 12_345


def run_dbt(env: dict[str, str], *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--project-dir", str(ROOT / "dbt"),
         "--profiles-dir", str(ROOT / "dbt"),
         "--target", "dev", *args],
        capture_output=True, text=True, env=env, check=False,
    )
    if result.returncode != 0:
        print(result.stdout[-4000:])
        raise SystemExit("dbt build failed")


def snapshot(warehouse: Path) -> list[tuple]:
    """Every row of the fact table, ordered — the thing that must not change."""
    with duckdb.connect(str(warehouse), read_only=True) as con:
        return con.sql(
            "select order_line_key, square_order_id, square_line_item_uid, "
            "       sale_date, quantity, net_sales_cents "
            "from main_marts.fact_order_line order by order_line_key"
        ).fetchall()


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="retailpulse-incremental-"))
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

    print("1. Generating a fixture and building the warehouse from scratch")
    import generate_synthetic_bronze as synth

    sys.argv = ["generate_synthetic_bronze", str(bronze), "--days", "120"]
    synth.main()

    from retailpulse.reference_inputs import ensure_reference_inputs
    from retailpulse.transform.silver import run_silver_transform

    run_silver_transform(bronze, silver)
    ensure_reference_inputs(input_dir=inputs, warehouse_path=warehouse)
    run_dbt(env, "--full-refresh")
    baseline = snapshot(warehouse)
    print(f"   {len(baseline):,} rows")

    print("2. Running incrementally with nothing changed — must be a no-op")
    run_dbt(env)
    after_noop = snapshot(warehouse)
    if after_noop != baseline:
        print("   FAIL: an incremental run with no new data changed the table")
        return 1
    print("   identical")

    print("3. Amending an order that closed weeks ago")
    orders = silver / "order_lines.parquet"
    with duckdb.connect() as con:
        target = con.sql(
            f"select order_id, line_item_uid, closed_at, order_updated_at, net_sales_cents "
            f"from read_parquet('{orders.as_posix()}') "
            f"where closed_at is not null "
            f"order by closed_at limit 1"
        ).fetchone()
        order_id, line_uid, closed_at, updated_at, old_net = target

        # The edit Square would make: the money changes and `order_updated_at`
        # moves to now, while `closed_at` stays back when the sale happened.
        # That is precisely the row a closed_at-based filter cannot see.
        #
        # Silver holds timestamps as the strings Square sent, and staging is
        # what casts them, so the replacement has to be an ISO string too —
        # writing a real TIMESTAMP here changes the column type and the
        # comparison stops being like for like.
        con.execute(f"""
            create table amended as
            select * exclude (net_sales_cents, order_updated_at),
                   case when order_id = ? and line_item_uid = ?
                        then net_sales_cents + {PRICE_BUMP}
                        else net_sales_cents end as net_sales_cents,
                   case when order_id = ? and line_item_uid = ?
                        then strftime(current_localtimestamp(), '%Y-%m-%dT%H:%M:%S.000Z')
                        else order_updated_at end as order_updated_at
            from read_parquet('{orders.as_posix()}')
        """, [order_id, line_uid, order_id, line_uid])
        # Column order must survive the rewrite, or the staging model's
        # positional expectations break.
        cols = [c for c in con.sql(
            f"select * from read_parquet('{orders.as_posix()}') limit 0").columns]
        con.execute(
            f"copy (select {', '.join(cols)} from amended) "
            f"to '{orders.as_posix()}' (format parquet)"
        )

    newest_closed = duckdb.connect().sql(
        f"select max(closed_at) from read_parquet('{orders.as_posix()}')"
    ).fetchone()[0]
    print(f"   order {order_id} line {line_uid}")
    print(f"   closed_at   {closed_at}  (unchanged, and older than the newest "
          f"closed_at of {newest_closed})")
    print(f"   net sales   {old_net} -> {old_net + PRICE_BUMP}")

    print("4. Running incrementally — the edit must arrive")
    run_dbt(env)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        got = con.sql(
            "select net_sales_cents from main_marts.fact_order_line "
            "where square_order_id = ? and square_line_item_uid = ?",
            params=[order_id, line_uid],
        ).fetchone()
    if got is None:
        print("   FAIL: the amended line is missing from the fact table")
        return 1
    if Decimal(str(got[0])) != Decimal(str(old_net)) + PRICE_BUMP:
        print(f"   FAIL: fact table still shows {got[0]}, expected "
              f"{old_net + PRICE_BUMP}. A late-arriving edit was missed — "
              f"which is exactly what filtering on closed_at would do.")
        return 1
    print(f"   fact table shows {got[0]}")

    print("5. Comparing the incremental result against a full rebuild")
    incremental_result = snapshot(warehouse)
    run_dbt(env, "--full-refresh")
    rebuilt = snapshot(warehouse)
    if incremental_result != rebuilt:
        only_inc = set(incremental_result) - set(rebuilt)
        only_full = set(rebuilt) - set(incremental_result)
        print(f"   FAIL: {len(only_inc)} rows only in the incremental result, "
              f"{len(only_full)} only in the rebuild")
        for row in list(only_inc)[:3]:
            print(f"     incremental only: {row}")
        for row in list(only_full)[:3]:
            print(f"     rebuild only:     {row}")
        return 1
    print(f"   identical — {len(rebuilt):,} rows, row for row")

    shutil.rmtree(workdir, ignore_errors=True)
    print("\nIncremental build verified: no-op safe, catches late-arriving "
          "edits, and agrees with a full rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
