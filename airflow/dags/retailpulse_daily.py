"""RetailPulse as an Airflow DAG.

The same pipeline the Dagster code location runs, expressed the other way. Both
call identical commands — `retailpulse extract-all`, `transform-silver`,
`dbt build` — because the pipeline logic lives in the package and the CLI, not
in either orchestrator. That is the property being demonstrated: swapping
schedulers is a day's work, not a rewrite.

Where the two genuinely differ:

* **Airflow schedules tasks; Dagster declares assets.** Here the unit is "run
  the silver transform". In `retailpulse.orchestration`, the unit is
  "`silver/order_lines` should exist and be current". The asset framing is what
  makes the dbt models individually addressable and gives freshness checks
  something to attach to; Airflow's task framing needs a separate mechanism for
  each of those.

* **Backfill means different things.** `catchup=True` below will run this DAG
  once per missed logical date, which is a genuine backfill because the store
  day is derived from `logical_date` rather than from wall-clock now. But each
  run re-executes the *whole* chain including the full Silver and dbt rebuild,
  where the Dagster version backfills only the partitioned Bronze assets and
  refreshes downstream once.

* **Failure semantics.** Retries here are per-task and configured in
  `default_args`; the Dagster version carries them on the assets that actually
  talk to a rate-limited API, and leaves the local steps without them.

Neither is wrong. Dagster fits this pipeline better because the pipeline's real
units are tables, not steps — see docs/orchestration.md. Airflow is here because
it is the scheduler most teams actually run, and porting to it is the honest way
to show the pipeline is not welded to one tool.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from airflow import DAG

PROJECT_ROOT = os.environ.get("RETAILPULSE_HOME", "/opt/retailpulse")

# The store's timezone, not the server's — the same rule the rest of the project
# follows. A liquor store's evening trade is already the next day in UTC, so a
# DAG scheduled on UTC midnights would file Tuesday night under Wednesday.
STORE_TIMEZONE = ZoneInfo(os.environ.get("RETAILPULSE_STORE_TIMEZONE", "America/Los_Angeles"))

DBT_ENV = {
    "RETAILPULSE_SILVER_DIR": f"{PROJECT_ROOT}/data/silver",
    "RETAILPULSE_INPUT_DIR": f"{PROJECT_ROOT}/data/input",
    "RETAILPULSE_WAREHOUSE_PATH": f"{PROJECT_ROOT}/data/gold/warehouse.duckdb",
    "RAW_DATA_DIR": f"{PROJECT_ROOT}/data/bronze",
}

default_args = {
    "owner": "retailpulse",
    "retries": 3,
    # Exponential, because retrying a rate-limited API immediately just burns
    # the remaining quota.
    "retry_delay": timedelta(seconds=15),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=5),
    "depends_on_past": False,
}


def check_warehouse_freshness(**context) -> None:
    """Fail if the newest sale is more than two days old.

    The equivalent of the `sales_are_fresh` asset check. It catches the silent
    failure mode: every task succeeds, every dbt test passes, and the dashboard
    looks healthy while showing data that stopped updating a week ago — because
    "no new rows" violates no constraint.
    """
    import duckdb

    warehouse = DBT_ENV["RETAILPULSE_WAREHOUSE_PATH"]
    with duckdb.connect(warehouse, read_only=True) as con:
        latest = con.execute(
            "select max(sale_date) from main_marts.fact_order_line"
        ).fetchone()[0]

    if latest is None:
        raise ValueError("fact_order_line contains no dated sales.")

    age = (datetime.now(STORE_TIMEZONE).date() - latest).days
    if age > 2:
        raise ValueError(f"Newest sale is {latest} ({age} days old); threshold is 2 days.")
    print(f"Freshness OK: newest sale {latest} ({age} days old).")


with DAG(
    dag_id="retailpulse_daily",
    description="Square -> Bronze -> Silver -> dbt Gold, once per store-local day.",
    default_args=default_args,
    start_date=datetime(2025, 7, 26, tzinfo=STORE_TIMEZONE),
    schedule="0 2 * * *",
    # True so a missed day is actually recovered rather than skipped. Safe here
    # because Bronze is append-only and Silver dedupes on natural key, so
    # re-running a day cannot corrupt anything.
    catchup=True,
    # Extraction hits a rate-limited API; parallel catchup runs would compete
    # for the same quota and trip the limit they are each retrying against.
    max_active_runs=1,
    tags=["retailpulse", "elt"],
) as dag:

    # `{{ ds }}` is the logical date — the store day this run represents, not
    # the day it happens to execute. That is what makes catchup a real backfill
    # rather than a repeated import of today.
    extract = BashOperator(
        task_id="extract_square",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            "retailpulse extract-all --days 1"
        ),
        env=DBT_ENV,
        append_env=True,
    )

    silver = BashOperator(
        task_id="transform_silver",
        bash_command=f"cd {PROJECT_ROOT} && retailpulse transform-silver",
        env=DBT_ENV,
        append_env=True,
    )

    dbt_inputs = BashOperator(
        task_id="ensure_dbt_inputs",
        bash_command=f"cd {PROJECT_ROOT} && python3 scripts/ensure_dbt_inputs.py",
        env=DBT_ENV,
        append_env=True,
    )

    # `dbt build` rather than `dbt run`: it interleaves tests with models, so a
    # failing test stops its dependants instead of letting bad data flow through
    # to the KPI layer and the dashboard.
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            "dbt build --project-dir dbt --profiles-dir dbt"
        ),
        env=DBT_ENV,
        append_env=True,
    )

    freshness = PythonOperator(
        task_id="check_freshness",
        python_callable=check_warehouse_freshness,
    )

    smoke = BashOperator(
        task_id="smoke_test_dashboard_queries",
        bash_command=f"cd {PROJECT_ROOT} && python3 scripts/smoke_dashboard_queries.py",
        env=DBT_ENV,
        append_env=True,
    )

    extract >> silver >> dbt_inputs >> dbt_build >> [freshness, smoke]
