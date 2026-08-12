#!/usr/bin/env python3
"""Push the current Silver layer to every cloud warehouse that is configured.

Local builds are one command (`make dbt-build`); keeping Snowflake and BigQuery
level with them was four more, run by hand, in the right order. Forgetting is
not loud — the cloud copies simply carry yesterday's numbers, and the first
sign is `compare_warehouses.py` disagreeing about totals.

So this does the whole thing:

    for each configured warehouse:
        load Silver          (Parquet -> tables; each warehouse has its own
                              loader, because the models are portable and the
                              ingestion is not)
        dbt build            (against that target)

Targets with no credentials in the environment are skipped with a note rather
than failing, so a clone with no cloud accounts still runs it happily and gets
told why nothing happened. Exits non-zero only if a warehouse that *is*
configured fails.

Run it after `make silver`, or use `make sync-cloud` which does both:

    python3 scripts/sync_warehouses.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def snowflake_ready() -> str | None:
    if not os.environ.get("SNOWFLAKE_ACCOUNT"):
        return "SNOWFLAKE_ACCOUNT is not set"
    key = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    if not key or not Path(key).is_file():
        return "SNOWFLAKE_PRIVATE_KEY_PATH does not point at a key file"
    return None


def bigquery_ready() -> str | None:
    if not os.environ.get("BIGQUERY_PROJECT"):
        return "BIGQUERY_PROJECT is not set"
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds or not Path(creds).is_file():
        return "GOOGLE_APPLICATION_CREDENTIALS does not point at a key file"
    return None


# (label, dbt target, loader script, readiness check)
WAREHOUSES = [
    ("Snowflake", "snowflake", "load_silver_to_snowflake.py", snowflake_ready),
    ("BigQuery", "prod", "load_silver_to_bigquery.py", bigquery_ready),
]


def run(command: list[str], env: dict[str, str]) -> tuple[bool, str]:
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def main() -> int:
    silver = Path(os.environ.get("RETAILPULSE_SILVER_DIR", "data/silver"))
    if not silver.is_dir() or not any(silver.glob("*.parquet")):
        print(f"No Silver Parquet found at {silver} — run `make silver` first.", file=sys.stderr)
        return 1

    failures, synced, skipped = [], [], []

    for label, target, loader, ready in WAREHOUSES:
        reason = ready()
        if reason:
            print(f"  skipping {label}: {reason}")
            skipped.append(label)
            continue

        print(f"\n  {label} — loading Silver…")
        started = time.monotonic()
        ok, output = run([sys.executable, str(ROOT / "scripts" / loader)], os.environ.copy())
        if not ok:
            print(f"  {label} FAILED while loading Silver:")
            print("    " + "\n    ".join(output.strip().splitlines()[-8:]))
            failures.append(label)
            continue
        for line in output.strip().splitlines():
            if line.strip().startswith("loaded"):
                print(f"  {line.strip()}")

        print(f"  {label} — building models…")
        env = os.environ.copy()
        env["RETAILPULSE_DBT_TARGET"] = target
        ok, output = run(
            [sys.executable, "-m", "dbt.cli.main", "build",
             "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt")],
            env,
        )
        summary = next(
            (ln.split("Done.")[-1].strip() for ln in output.splitlines() if "Done." in ln), ""
        )
        if not ok:
            print(f"  {label} FAILED to build: {summary}")
            # The failing node names are the useful part; dbt puts them under
            # "Failure in".
            for line in output.splitlines():
                if "Failure in" in line or "Error in" in line:
                    print(f"    {line.strip()}")
            failures.append(label)
            continue

        print(f"  {label} built in {time.monotonic() - started:.0f}s — {summary}")
        synced.append(label)

    print()
    if synced:
        print(f"Synced: {', '.join(synced)}")
    if skipped:
        print(f"Skipped (not configured): {', '.join(skipped)}")
    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    if not synced:
        print("Nothing to do — no cloud warehouse is configured in this environment.")
        return 0

    print("\nCheck they agree: python3 scripts/compare_warehouses.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
