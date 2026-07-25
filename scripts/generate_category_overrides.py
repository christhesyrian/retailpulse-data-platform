#!/usr/bin/env python3
"""Generate a starter category-override map from the current Silver catalog.

Lists every distinct raw category name (with how many items and how much it
sold, if a warehouse is available) and pre-fills `canonical_category` with the
generic-normalized value (uppercase/trim). You then edit the rows that are
typos or synonyms — e.g. change `SCTRATCHER`'s canonical to `SCRATCHER` — and
rebuild. Rows left unchanged are ignored (raw already equals canonical).

Output goes to `data/input/category_overrides.csv` (git-ignored), so your real
category taxonomy stays local. The Square catalog is never modified.

Usage:
    python3 scripts/generate_category_overrides.py [--silver-dir DIR] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import duckdb


def normalize(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silver-dir", default="data/silver")
    parser.add_argument("--out", default="data/input/category_overrides.csv")
    args = parser.parse_args()

    catalog = Path(args.silver_dir) / "catalog_items.parquet"
    if not catalog.exists():
        print(f"Catalog not found at {catalog}. Run `make silver` (or demo-data) first.")
        return 1

    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"""
        select category_name, count(*) as items
        from read_parquet('{catalog.as_posix()}')
        where category_name is not null
        group by category_name
        order by items desc
        """
    ).fetchall()
    con.close()

    # Collapse to distinct generic-normalized categories (so 'Beer'/'BEER' show once).
    seen: dict[str, int] = {}
    for category_name, items in rows:
        key = normalize(category_name)
        if key:
            seen[key] = seen.get(key, 0) + items

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["raw_category", "canonical_category"])
        for key in sorted(seen, key=lambda k: -seen[k]):
            # canonical pre-filled = raw; edit the typo/synonym rows by hand.
            writer.writerow([key, key])

    print(
        f"Wrote {len(seen)} category rows to {out_path}.\n"
        "Edit the 'canonical_category' column for typos/synonyms (e.g. "
        "SCTRATCHER -> SCRATCHER), then run `make dbt-build`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
