#!/usr/bin/env python3
"""Create the optional operator-maintained input files dbt expects.

`vendor_costs.csv` and `category_overrides.csv` are both optional features:
a store with no vendor costs still gets sales KPIs, and a store that needs no
category renaming still gets a clean catalog. But the staging models read them
unconditionally, so the files have to *exist* — an empty file with just a
header is what "I have none of these" looks like to dbt.

This lives in one place because it was previously inlined in the Makefile
only. CI runs `dbt build` directly rather than through make, so it never
created them, and the build failed on a missing file the moment anything else
stopped failing first. Both callers now run this.

Existing files are never touched, so real vendor costs survive a rebuild.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# (relative path, header row) — the header is the contract the staging model
# reads, so it has to match stg_vendor_costs.sql / stg_category_overrides.sql.
INPUTS: list[tuple[str, str]] = [
    ("vendor_costs.csv", "variation_id,item_name,category_name,vendor_name,unit_cost_cents"),
    ("category_overrides.csv", "raw_category,canonical_category"),
]


def main() -> int:
    input_dir = Path(os.environ.get("RETAILPULSE_INPUT_DIR", "data/input"))
    warehouse = Path(
        os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
    )

    input_dir.mkdir(parents=True, exist_ok=True)
    warehouse.parent.mkdir(parents=True, exist_ok=True)

    for name, header in INPUTS:
        path = input_dir / name
        if path.exists():
            print(f"  kept: {path} ({path.stat().st_size} bytes)")
            continue
        path.write_text(header + "\n", encoding="utf-8")
        print(f"  created empty: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
