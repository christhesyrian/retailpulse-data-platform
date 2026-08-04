"""The pipeline as Dagster assets: Square -> Bronze -> Silver -> dbt Gold.

Design notes worth knowing before changing anything here.

**The extraction logic is wrapped, not reimplemented.** Every Square asset calls
the same `retailpulse.extract.jobs` functions the CLI does. Dagster supplies the
run id, the window and the retry behaviour; it does not own any knowledge of how
Square paginates. That keeps `retailpulse extract-all` working as a
credential-free escape hatch when the orchestrator is not running.

**Only the transactional assets are partitioned.** Orders and payments are
event streams, so "one store-local day" is a meaningful, backfillable unit.
Locations, catalog and inventory are *current-state* snapshots — Square's API
returns what is true now, with no historical window to ask for — so partitioning
them would invent a history that cannot be fetched. They are plain snapshot
assets, and that asymmetry is deliberate rather than an oversight.

**Bronze stays append-only.** Re-running a partition does not overwrite the
earlier file; it writes a second immutable snapshot alongside it, because the
first one is still a truthful record of what Square returned at that time.
Idempotency is achieved one layer later: `run_silver_transform` dedupes on each
table's natural key, latest-write-wins. So a backfill is safe to re-run without
ever mutating raw data.
"""

# NOTE: no `from __future__ import annotations` in this module, and don't add
# it. Dagster resolves the `context` parameter's annotation at decoration time
# to decide what to inject; PEP 563 turns that annotation into the *string*
# "AssetExecutionContext", which Dagster cannot match against the class. The
# failure is a confusing "Cannot annotate `context` parameter with type
# AssetExecutionContext" that names the very type you used. Python 3.11 handles
# the builtin generics used below without the import anyway.

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetOut,
    Backoff,
    Jitter,
    MaterializeResult,
    MetadataValue,
    RetryPolicy,
    asset,
    multi_asset,
)

from retailpulse.config import Settings
from retailpulse.extract.jobs import (
    extract_catalog,
    extract_inventory,
    extract_locations,
    extract_orders,
    extract_payments,
    list_active_location_ids,
)
from retailpulse.orchestration.partitions import (
    STORE_TIMEZONE,
    daily_partitions,
    partition_utc_window,
)
from retailpulse.orchestration.resources import paths, square_client
from retailpulse.reference_inputs import REFERENCE_INPUTS, ensure_reference_inputs
from retailpulse.transform.silver import run_silver_transform

# Square rate-limits, and a scheduled pipeline must survive that without a human
# reading the logs at 2am. Exponential backoff with jitter so that a backfill
# launching many partitions at once does not retry them all in lockstep.
SQUARE_RETRY = RetryPolicy(
    max_retries=3,
    delay=15,
    backoff=Backoff.EXPONENTIAL,
    jitter=Jitter.PLUS_MINUS,
)

BRONZE_GROUP = "bronze_square"
SILVER_GROUP = "silver"
REFERENCE_GROUP = "reference"

# Must match the table names written by run_silver_transform, which are in turn
# the dbt source table names in dbt/models/staging/_sources.yml. The asset keys
# below are ["silver", <name>], which is exactly how dagster-dbt keys a dbt
# source — that identity is what joins the Python assets to the dbt graph.
SILVER_TABLES = ("locations", "catalog_items", "order_lines", "payments", "inventory_snapshots")


def _environment() -> str:
    return Settings().square_environment


@asset(
    group_name=BRONZE_GROUP,
    retry_policy=SQUARE_RETRY,
    description="Square locations snapshot, and the source of the location dimension.",
)
def square_locations(context: AssetExecutionContext) -> MaterializeResult:
    with square_client() as client:
        location_ids = extract_locations(client, paths.bronze, context.run_id, _environment())

    if not location_ids:
        # Not a soft warning: every downstream extract is scoped by location, so
        # an empty list would silently produce an empty, healthy-looking day.
        raise ValueError("Square returned no ACTIVE locations; refusing to extract an empty window.")

    return MaterializeResult(
        metadata={
            "active_locations": len(location_ids),
            "environment": _environment(),
        }
    )


@asset(
    group_name=BRONZE_GROUP,
    retry_policy=SQUARE_RETRY,
    description="Square catalog snapshot: items, variations and categories as they are now.",
)
def square_catalog(context: AssetExecutionContext) -> MaterializeResult:
    with square_client() as client:
        pages = extract_catalog(client, paths.bronze, context.run_id, _environment())
    return MaterializeResult(metadata={"pages": pages})


@asset(
    group_name=BRONZE_GROUP,
    partitions_def=daily_partitions,
    retry_policy=SQUARE_RETRY,
    deps=[square_locations],
    description="One store-local day of Square orders. Backfillable; the unit of replay.",
)
def square_orders(context: AssetExecutionContext) -> MaterializeResult:
    begin_time, end_time = partition_utc_window(context.partition_key)
    with square_client() as client:
        # Resolved live rather than passed in from the `square_locations` asset.
        # A backfilled partition should depend on what Square says now, not on a
        # pickled value left behind by whichever unrelated run last materialized
        # that asset — that indirection is invisible until it is wrong.
        location_ids = list_active_location_ids(client)
        pages = extract_orders(
            client,
            paths.bronze,
            location_ids,
            begin_time,
            end_time,
            context.run_id,
            _environment(),
        )
    return MaterializeResult(
        metadata={
            "pages": pages,
            "store_day": context.partition_key,
            "store_timezone": STORE_TIMEZONE,
            # Surfaced because it is the single most useful thing to eyeball when
            # a partition looks wrong: on DST boundaries this window is 23h or 25h.
            "utc_window": f"{begin_time} -> {end_time}",
        }
    )


@asset(
    group_name=BRONZE_GROUP,
    partitions_def=daily_partitions,
    retry_policy=SQUARE_RETRY,
    description="One store-local day of Square payments. Backfillable; the unit of replay.",
)
def square_payments(context: AssetExecutionContext) -> MaterializeResult:
    begin_time, end_time = partition_utc_window(context.partition_key)
    with square_client() as client:
        pages = extract_payments(
            client, paths.bronze, begin_time, end_time, context.run_id, _environment()
        )
    return MaterializeResult(
        metadata={
            "pages": pages,
            "store_day": context.partition_key,
            "utc_window": f"{begin_time} -> {end_time}",
        }
    )


@asset(
    group_name=BRONZE_GROUP,
    retry_policy=SQUARE_RETRY,
    deps=[square_locations],
    description=(
        "Current stock counts per location. Deliberately unpartitioned: Square reports "
        "inventory as of now and exposes no historical window, so there is nothing to backfill."
    ),
)
def square_inventory(context: AssetExecutionContext) -> MaterializeResult:
    with square_client() as client:
        location_ids = list_active_location_ids(client)
        pages = extract_inventory(
            client, paths.bronze, location_ids, context.run_id, _environment()
        )
    return MaterializeResult(metadata={"pages": pages})


@multi_asset(
    outs={
        "vendor_costs": AssetOut(key=AssetKey(["reference", "vendor_costs"]), is_required=False),
        "category_overrides": AssetOut(
            key=AssetKey(["reference", "category_overrides"]), is_required=False
        ),
    },
    group_name=REFERENCE_GROUP,
    can_subset=False,
    description=(
        "Operator-maintained CSVs dbt reads as `reference` sources. Creates them header-only "
        "when absent so the optional margin and category-override features degrade to "
        "'no coverage' instead of failing the build. Never overwrites real data."
    ),
)
def reference_inputs(context: AssetExecutionContext):
    outcomes = ensure_reference_inputs(paths.input, paths.warehouse)
    for filename, _header in REFERENCE_INPUTS:
        name = filename.removesuffix(".csv")
        path = paths.input / filename
        yield MaterializeResult(
            asset_key=AssetKey(["reference", name]),
            metadata={
                "outcome": outcomes[filename],
                "path": MetadataValue.path(str(path)),
                "bytes": path.stat().st_size,
            },
        )


@multi_asset(
    outs={name: AssetOut(key=AssetKey(["silver", name])) for name in SILVER_TABLES},
    deps=[square_locations, square_catalog, square_orders, square_payments, square_inventory],
    group_name=SILVER_GROUP,
    can_subset=False,
    description=(
        "Rebuild every Silver Parquet table from the full Bronze history. One computation "
        "produces all five tables, so this is a multi_asset rather than five assets that "
        "would each re-read Bronze. This is where re-run idempotency comes from: Bronze "
        "appends, Silver dedupes on natural key with latest-write-wins."
    ),
)
def silver_tables(context: AssetExecutionContext):
    counts = run_silver_transform(paths.bronze, paths.silver)
    context.log.info("Silver rebuild complete: %s", counts)
    for name in SILVER_TABLES:
        yield MaterializeResult(
            asset_key=AssetKey(["silver", name]),
            metadata={
                "rows": counts[name],
                "path": MetadataValue.path(str(paths.silver / f"{name}.parquet")),
            },
        )
