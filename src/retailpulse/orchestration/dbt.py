"""The dbt project, loaded as Dagster assets.

`@dbt_assets` turns all 30 models into individual assets with their real
lineage, rather than one opaque "run dbt" step. That matters for two reasons
beyond the picture: a failure names the model that failed, and a downstream-only
rebuild (`kpi_*` after a marts change) is expressible without a full build.

The join between the Python assets and the dbt graph is by asset key, not by
configuration. dagster-dbt keys a dbt source as [source_name, table_name], so
`silver.order_lines` in dbt/models/staging/_sources.yml becomes
AssetKey(["silver", "order_lines"]) — which is exactly the key the
`silver_tables` multi_asset emits. Rename one side and the graph silently
splits into two disconnected halves, so the names are load-bearing.
"""

# No `from __future__ import annotations` here — see the note in assets.py;
# it breaks Dagster's resolution of the `context` parameter type.

from collections.abc import Iterator

from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets

from retailpulse.orchestration.resources import dbt_project

DBT_GROUP = "gold_dbt"


@dbt_assets(manifest=dbt_project.manifest_path)
def retailpulse_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator:
    """Run `dbt build`, so every model is materialized and every test runs.

    `build` rather than `run` is deliberate: it interleaves tests with models,
    so a failing test stops its dependants instead of letting bad data flow
    into the KPI layer and the dashboard.
    """
    yield from dbt.cli(["build"], context=context).stream()
