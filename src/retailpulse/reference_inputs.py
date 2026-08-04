"""Operator-maintained input files that dbt reads as `reference` sources.

`vendor_costs.csv` and `category_overrides.csv` are both optional features: a
store with no vendor costs still gets sales KPIs, and a store that needs no
category renaming still gets a clean catalog. But the staging models read them
unconditionally, so the files have to *exist* — an empty file with just a
header is what "I have none of these" looks like to dbt.

This lives in the package rather than in a script because three callers need
it: `make dbt-build`, the CI workflow, and the Dagster `reference_inputs`
asset. It was previously inlined in the Makefile only, which is how CI ended up
failing on a missing file the moment anything else stopped failing first.

Existing files are never touched, so real vendor costs survive a rebuild.
"""

from __future__ import annotations

from pathlib import Path

# (relative path, header row) — the header is the contract the staging model
# reads, so it has to match stg_vendor_costs.sql / stg_category_overrides.sql.
REFERENCE_INPUTS: list[tuple[str, str]] = [
    ("vendor_costs.csv", "variation_id,item_name,category_name,vendor_name,unit_cost_cents"),
    ("category_overrides.csv", "raw_category,canonical_category"),
]


def ensure_reference_inputs(input_dir: Path, warehouse_path: Path) -> dict[str, str]:
    """Create any missing reference CSV as header-only; leave existing ones alone.

    Also creates the directories dbt writes into, so a clean checkout can build
    without any manual `mkdir`. Returns {filename: "created" | "kept"} so
    callers can report what they did.
    """

    input_dir.mkdir(parents=True, exist_ok=True)
    warehouse_path.parent.mkdir(parents=True, exist_ok=True)

    outcomes: dict[str, str] = {}
    for name, header in REFERENCE_INPUTS:
        path = input_dir / name
        if path.exists():
            outcomes[name] = "kept"
            continue
        path.write_text(header + "\n", encoding="utf-8")
        outcomes[name] = "created"
    return outcomes
