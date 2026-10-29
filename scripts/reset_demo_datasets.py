#!/usr/bin/env python3
"""Drop the synthetic demo datasets in BigQuery so they can be rebuilt clean.

`make demo-cloud` needs this for the same reason `make demo-warehouse` deletes
its DuckDB file: two kinds of state survive a rebuild and neither is recomputed.

  * `fact_order_line` is incremental. Rows built from a previous fixture stay
    exactly as they were — so when the synthetic store's timezone changed from
    UTC to America/Los_Angeles, 8,371 stale rows kept their old local timestamps
    and failed `assert_local_time_conversion` while every new row passed.

  * The catalog snapshot is, by design, the one thing that cannot be rebuilt
    from Bronze. Regenerating the fixture with a different catalog would be
    recorded as 218 items appearing and the old 16 being deleted — SCD history
    for a change that never happened to anything real.

Locally the answer is to delete a file. In BigQuery it is to drop the datasets,
which is why this exists as a script rather than a flag.

**It refuses to touch anything that is not clearly synthetic.** Every dataset it
deletes must start with the configured prefix (default `demo`), so pointing it
at a production dataset fails instead of succeeding. That guard is the whole
reason this is not a one-line `bq rm` in the Makefile: the destructive version
of this command and the safe version differ by one word.

Usage:
    python3 scripts/reset_demo_datasets.py
    python3 scripts/reset_demo_datasets.py --prefix demo --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix", default="demo",
        help="Only datasets whose name is this or starts with '<prefix>_' are "
             "eligible (default: demo).")
    parser.add_argument(
        "--dry-run", action="store_true", help="List what would be dropped.")
    args = parser.parse_args()

    prefix = args.prefix.strip().rstrip("_")
    if not prefix:
        print("--prefix cannot be empty: that would match every dataset.", file=sys.stderr)
        return 2

    project = os.environ.get("BIGQUERY_PROJECT", "")
    if not project:
        print("BIGQUERY_PROJECT is not set.", file=sys.stderr)
        return 1
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not credentials or not Path(credentials).is_file():
        print("GOOGLE_APPLICATION_CREDENTIALS does not point at a key file.", file=sys.stderr)
        return 1

    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    eligible = [
        d.dataset_id for d in client.list_datasets()
        if d.dataset_id == prefix or d.dataset_id.startswith(f"{prefix}_")
    ]

    if not eligible:
        print(f"Nothing to drop — no dataset in {project} starts with '{prefix}'.")
        return 0

    for dataset_id in eligible:
        # Belt and braces. The listing above already filtered, but this is the
        # line that actually deletes data, so it re-checks rather than trusting
        # that the filter above is still the filter above after an edit.
        if dataset_id != prefix and not dataset_id.startswith(f"{prefix}_"):
            print(f"REFUSING to drop {dataset_id}: does not match '{prefix}'.", file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"  would drop {project}.{dataset_id}")
            continue
        client.delete_dataset(f"{project}.{dataset_id}",
                             delete_contents=True, not_found_ok=True)
        print(f"  dropped {project}.{dataset_id}")

    if args.dry_run:
        print(f"\n{len(eligible)} dataset(s) would be dropped. Re-run without --dry-run.")
    else:
        print(f"\n{len(eligible)} dataset(s) dropped. `make demo-cloud` rebuilds them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
