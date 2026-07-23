#!/usr/bin/env python3
"""Generate a vendor-cost CSV from the current Silver catalog.

Vendor/acquisition costs are NOT in Square — they're an operator-maintained
input. This script reads the Silver catalog and writes one row per item
variation to `data/input/vendor_costs.csv`, filling in a synthetic vendor
and unit cost (a deterministic fraction of the sale price).

It serves two purposes:
  1. Demo/CI: produces a fully synthetic cost dataset so the margin models
     have data to run against with no real information.
  2. Template: run it against your real (Square Sandbox/Production) catalog
     to get every variation pre-listed, then edit `unit_cost_cents` and
     `vendor_name` with your real figures. `data/input/` is git-ignored, so
     real cost data never gets committed.

Usage:
    python3 scripts/generate_synthetic_vendor_costs.py [--silver-dir DIR] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import duckdb

SEED = 20260722

# Deterministic fake vendor per category (generic — not from any real store).
VENDOR_BY_CATEGORY = {
    "Beer": "Craft Beverage Distributors",
    "Wine": "Vineyard Imports Co",
    "Liquor": "Premier Spirits Supply",
    "Non-Alcoholic and Miscellaneous": "General Goods Wholesale",
}
DEFAULT_VENDOR = "Unassigned Vendor"

FIELDNAMES = ["variation_id", "item_name", "category_name", "vendor_name", "unit_cost_cents"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silver-dir", default="data/silver")
    parser.add_argument("--out", default="data/input/vendor_costs.csv")
    args = parser.parse_args()

    catalog_path = Path(args.silver_dir) / "catalog_items.parquet"
    if not catalog_path.exists():
        print(f"Catalog not found at {catalog_path}. Run `make silver` (or demo-data) first.")
        return 1

    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"""
        select variation_id, item_name, category_name, price_cents
        from read_parquet('{catalog_path.as_posix()}')
        order by item_name
        """
    ).fetchall()
    con.close()

    rng = random.Random(SEED)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for variation_id, item_name, category_name, price_cents in rows:
            # Cost is a deterministic 55–72% of sale price (retail liquor margins).
            ratio = 0.55 + rng.random() * 0.17
            unit_cost_cents = round((price_cents or 0) * ratio)
            writer.writerow(
                {
                    "variation_id": variation_id,
                    "item_name": item_name,
                    "category_name": category_name,
                    "vendor_name": VENDOR_BY_CATEGORY.get(category_name, DEFAULT_VENDOR),
                    "unit_cost_cents": unit_cost_cents,
                }
            )
            written += 1

    print(f"Wrote {written} vendor-cost rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
