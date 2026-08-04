#!/usr/bin/env python3
"""Create the optional operator-maintained input files dbt expects.

Thin CLI wrapper. The logic lives in `retailpulse.reference_inputs` so that
`make dbt-build`, the CI workflow and the Dagster `reference_inputs` asset all
create these files the same way — an earlier version existed only in the
Makefile, so CI (which runs `dbt build` directly) never created them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from retailpulse.reference_inputs import ensure_reference_inputs


def main() -> int:
    outcomes = ensure_reference_inputs(
        input_dir=Path(os.environ.get("RETAILPULSE_INPUT_DIR", "data/input")),
        warehouse_path=Path(
            os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
        ),
    )
    for name, outcome in outcomes.items():
        print(f"  {outcome}: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
