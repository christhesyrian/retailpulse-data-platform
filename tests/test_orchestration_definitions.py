"""Tests that the Dagster asset graph is wired the way the design intends.

These are cheap structural assertions, but they cover the failure mode that is
hardest to notice by eye: the Python assets and the dbt models are joined *by
asset key*, so renaming a Silver table or a dbt source silently splits the
graph into two disconnected halves that both still build. Nothing errors; you
just quietly lose lineage, and with it the ability to rebuild downstream of a
change. CI asserting the edges exist is what makes that loud.
"""

from dagster import AssetKey

from retailpulse.orchestration import assets
from retailpulse.orchestration.assets import SILVER_TABLES
from retailpulse.orchestration.definitions import defs


def _graph():
    return defs.resolve_asset_graph()


def test_definitions_load():
    """A syntax or wiring error here breaks the scheduler, not just a test."""
    assert defs.resolve_asset_graph().get_all_asset_keys()


def test_every_silver_table_feeds_a_dbt_staging_model():
    graph = _graph()

    for table in SILVER_TABLES:
        key = AssetKey(["silver", table])
        assert key in graph.get_all_asset_keys(), f"missing Silver asset {key}"

        children = graph.get(key).child_keys
        assert children, f"{key} feeds nothing — the Silver->dbt join is broken"
        assert all(child.path[0] == "staging" for child in children), (
            f"{key} should feed only staging models, got {children}"
        )


def test_reference_inputs_feed_their_staging_models():
    graph = _graph()

    for name in ("vendor_costs", "category_overrides"):
        key = AssetKey(["reference", name])
        assert graph.get(key).child_keys == {AssetKey(["staging", f"stg_{name}"])}


def test_transactional_assets_are_partitioned_and_snapshots_are_not():
    """The asymmetry is deliberate — see the module docstring in assets.py."""
    graph = _graph()

    for key in (AssetKey(["square_orders"]), AssetKey(["square_payments"])):
        assert graph.get(key).partitions_def is not None, f"{key} must be backfillable"

    for key in (
        AssetKey(["square_locations"]),
        AssetKey(["square_catalog"]),
        AssetKey(["square_inventory"]),
    ):
        # Square exposes no historical window for these, so a partition would
        # promise a backfill that cannot actually be performed.
        assert graph.get(key).partitions_def is None, f"{key} has no history to backfill"


def test_silver_depends_on_the_partitioned_bronze_assets():
    graph = _graph()
    parents = graph.get(AssetKey(["silver", "order_lines"])).parent_keys

    assert AssetKey(["square_orders"]) in parents
    assert AssetKey(["square_payments"]) in parents


def test_bronze_extraction_assets_retry():
    """Square rate-limits; an unattended schedule must survive that unaided."""
    for asset_def in (assets.square_orders, assets.square_payments, assets.square_locations):
        policy = asset_def.op.retry_policy
        assert policy is not None, f"{asset_def.key} would fail the whole run on a 429"
        assert policy.max_retries >= 1
        # Backoff matters as much as the retry count: retrying a rate-limit
        # immediately just burns the remaining quota.
        assert policy.backoff is not None


def test_both_jobs_and_schedules_are_defined():
    job_names = {job.name for job in defs.jobs}
    assert job_names == {"square_ingest_job", "warehouse_refresh_job"}

    # Schedules built from a partitioned job resolve lazily, so read them off
    # the repository rather than the raw Definitions list.
    schedules = defs.get_repository_def().schedule_defs
    assert len(schedules) == 2

    # Both must run on the store's clock, not the server's, or "yesterday"
    # drifts away from the merchant's trading day.
    assert {schedule.execution_timezone for schedule in schedules} == {"America/Los_Angeles"}

    # The refresh has to follow the ingest, not race it.
    by_name = {schedule.name: schedule.cron_schedule for schedule in schedules}
    assert by_name["square_ingest_job_schedule"] == "0 2 * * *"
    assert by_name["warehouse_refresh_schedule"] == "45 2 * * *"


def test_asset_checks_are_registered():
    check_keys = {
        check_key.name for definition in defs.asset_checks for check_key in definition.check_keys
    }
    assert check_keys == {"sales_are_fresh", "peak_hour_is_within_business_hours"}
