"""Jobs, schedules, and the `Definitions` object Dagster loads.

**Why there are two jobs rather than one.** Dagster requires every asset in a
job to share a partitioning scheme, and this pipeline legitimately has two:
orders and payments are per-store-day event streams, while the catalog, the
Silver rebuild and the dbt marts are full refreshes over all of history. Rather
than fake a partition on the refresh side — which would claim a per-day
rebuild that does not happen — the split is made explicit:

  square_ingest_job     partitioned, per store-day, the thing you backfill
  warehouse_refresh_job unpartitioned, rebuilds Silver and Gold from all Bronze

The refresh is scheduled 45 minutes after ingest. It is a time offset rather
than a hard dependency because the refresh is *correct at any time* — it reads
whatever Bronze holds. A late or failed ingest yields a warehouse that is one
day behind, not a broken one, and `sales_are_fresh` is what makes that visible.
"""

from __future__ import annotations

from dagster import (
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    build_schedule_from_partitioned_job,
    define_asset_job,
)
from dagster_dbt import DbtCliResource

from retailpulse.orchestration import assets, checks
from retailpulse.orchestration.dbt import retailpulse_dbt_assets
from retailpulse.orchestration.partitions import STORE_TIMEZONE
from retailpulse.orchestration.resources import dbt_executable, dbt_project

# --- Jobs ------------------------------------------------------------------

square_ingest_job = define_asset_job(
    name="square_ingest_job",
    selection=AssetSelection.assets(assets.square_orders, assets.square_payments),
    description="Extract one store-local day of orders and payments into Bronze.",
)

warehouse_refresh_job = define_asset_job(
    name="warehouse_refresh_job",
    selection=(
        AssetSelection.assets(
            assets.square_locations,
            assets.square_catalog,
            assets.square_inventory,
            assets.reference_inputs,
            assets.silver_tables,
        )
        | AssetSelection.assets(retailpulse_dbt_assets)
    ),
    description="Refresh the snapshot sources, rebuild Silver, then build and test every dbt model.",
)

# --- Schedules -------------------------------------------------------------

# Fires at 02:00 store time for the day that has just finished, so a run only
# ever covers a complete trading day. Anchoring on the store's timezone (not
# UTC) is what keeps "yesterday" meaning yesterday to the merchant.
square_ingest_schedule = build_schedule_from_partitioned_job(
    square_ingest_job,
    hour_of_day=2,
    minute_of_hour=0,
)

warehouse_refresh_schedule = ScheduleDefinition(
    name="warehouse_refresh_schedule",
    job=warehouse_refresh_job,
    cron_schedule="45 2 * * *",
    execution_timezone=STORE_TIMEZONE,
    description="Rebuild Silver and Gold 45 minutes after the day's ingest.",
)

# --- Definitions -----------------------------------------------------------

defs = Definitions(
    assets=[
        assets.square_locations,
        assets.square_catalog,
        assets.square_orders,
        assets.square_payments,
        assets.square_inventory,
        assets.reference_inputs,
        assets.silver_tables,
        retailpulse_dbt_assets,
    ],
    asset_checks=[
        checks.sales_are_fresh,
        checks.peak_hour_is_within_business_hours,
    ],
    jobs=[square_ingest_job, warehouse_refresh_job],
    schedules=[square_ingest_schedule, warehouse_refresh_schedule],
    resources={
        "dbt": DbtCliResource(project_dir=dbt_project, dbt_executable=dbt_executable()),
    },
)
