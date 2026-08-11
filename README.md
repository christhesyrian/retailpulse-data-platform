# RetailPulse

**A data platform over a real, operating liquor store's Square point-of-sale data** — extraction, a tested warehouse, orchestration, and a dashboard that answers the questions the owner actually asks.

[![CI](https://github.com/christhesyrian/retailpulse-data-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/christhesyrian/retailpulse-data-platform/actions/workflows/ci.yml)

![The RetailPulse dashboard: KPI tiles with period-over-period change, a daily sales trend with a 7-day average, category mix, and a popular-times grid](docs/img/dashboard-overview.png)

*The hosted demo runs on a generated fixture — see [Try it](#try-it).*

## What it does, concretely

A year of the store's trade lands in a tested warehouse, and the questions that used to need clicking through Square's reports become SQL:

- **Peak trading hour is 16:00**, not the evening the owner assumed. That number was wrong for a year — see [the local-time bug](#the-bug-worth-reading-about).
- **Revenue forecast for tomorrow, the next week and the next month**, which backtests itself: on a 28-day holdout it is typically **within 11.0% of actual takings**, against **16.2%** for a flat daily average. Both figures are published in the dashboard, because a forecast that doesn't say how wrong it usually is invites you to trust it completely.
- **Per-item units and revenue** for any window, each against the equivalent window before it, with a 4-week projection per item.

The interesting part is not the charts. It is that **every number on screen comes from a tested warehouse object** — the dashboard computes nothing — and that the whole thing builds identically on three different warehouses.

## Try it

```bash
git clone https://github.com/christhesyrian/retailpulse-data-platform
cd retailpulse-data-platform
make install
make demo-data     # synthetic Bronze -> Silver -> dbt Gold + KPIs. No Square account needed.
make dashboard     # http://localhost:8501
```

No credentials, no cloud account, no Square access. `make demo-data` generates a deterministic fixture describing a fictional store, then runs the real pipeline over it.

> On a fresh clone this is safe. If you have already extracted real data, note that `make demo-data` writes synthetic pages *alongside* whatever is in `data/bronze/` rather than replacing it, and the Silver rebuild then reads both — so point it at an empty tree, or clear Bronze first.

The hosted demo does the same thing on boot: [`streamlit_app.py`](streamlit_app.py) generates the fixture, runs the Bronze→Silver transform, and builds all 33 dbt models and 93 tests before the first page renders — about 15 seconds, once per container. **No real business data is present in this repository or reachable from the demo.**

## Architecture

```mermaid
flowchart LR
    A[Square APIs] --> B[Python extraction]
    V[Vendor costs CSV] --> E
    B --> C[Bronze: raw JSON]
    C --> D[Silver: Parquet lake]
    D --> E[Gold: facts & dimensions]
    E --> K[KPI, item sales & forecast]
    K --> F[Streamlit dashboard]
```

The pipeline runs as a **Dagster asset graph** — 42 assets, two scheduled jobs and two asset checks — with the dbt project loaded through `dagster-dbt`, so all 33 models appear as individual assets with real lineage rather than one opaque "run dbt" step. Orders and payments are partitioned by **store-local day**, so one day is the unit of both scheduling and backfill.

```bash
make dagster   # asset graph, run history and backfills at localhost:3000
```

| Layer | Status | Notes |
|---|---|---|
| Square APIs | Implemented (read-only) | Locations, catalog, orders, payments, inventory |
| Extraction | Implemented | Cursor-paginated, bounded retry/backoff, Sandbox by default with an explicit production opt-in |
| Bronze | Implemented | Immutable, partitioned, local, Git-ignored |
| Silver | Implemented | Deduplicated, flattened Parquet, rebuilt from Bronze |
| Gold | Implemented | 33 `dbt` models — dimensions, facts, and the KPI layer |
| Forecasting | Implemented | Per-item units (4 weeks) and revenue (31 days), both backtested |
| Dashboard | Implemented | Streamlit, reading only tested models |
| Orchestration | Implemented | Dagster: daily-partitioned ingest, retries, freshness and timezone asset checks, two schedules |
| Portability | Implemented | The same models build on DuckDB, Snowflake and BigQuery |

## The parts worth reviewing

If you are assessing this as engineering rather than as a dashboard, these are the four things to look at.

### One set of models, three warehouses — and a script that proves it

`dbt build` runs green on **DuckDB, Snowflake and BigQuery**: all 126 nodes, same models, same tests. Dialect differences are isolated behind adapter-dispatched macros in [`dbt/macros/portable.sql`](dbt/macros/portable.sql), and the parameterized range API in [`dbt/macros/range_api.sql`](dbt/macros/range_api.sql) publishes one body as a DuckDB table macro, a Snowflake SQL UDTF or a BigQuery table function.

The point is what that exercise turns up. A green build on three warehouses only proves the SQL *parses* on each — so [`scripts/compare_warehouses.py`](scripts/compare_warehouses.py) runs the same twelve queries against all three and demands the same answers back:

```bash
python3 scripts/compare_warehouses.py
```

It found a real bug that no build would ever catch: BigQuery's `extract(week from ...)` counts weeks from the first Sunday of the year, so `dim_date.week_of_year` read 29 where the ISO answer is 30, in a column no test asserted. The same class of thing hides in `dayname` ("Monday" on DuckDB, "Mon" on Snowflake) and `date_trunc(x, WEEK)` (Sunday on BigQuery, Monday elsewhere). All of them compile everywhere and mean different things.

Ten of the twelve checks require exact agreement. Two allow 0.01%, documented in the script: forecast units and backtest error are rounded floating-point regression output, and last-bit differences tip 14 of 8,432 rows across a rounding boundary by exactly one unit each.

### The bug worth reading about

Square records every sale in UTC. A store in California does its heaviest trade in the evening, which is already the next day in UTC — so reading the date straight off the timestamp put a year of Friday-evening sales on Saturday. **Every total still reconciled.** Daily revenue was right, monthly revenue was right, and the error was invisible until someone noticed the hour-of-day chart peaking at 2am.

Local time is now resolved once, in the fact layer, through the `to_local_time` macro, and [`dbt/tests/assert_local_time_conversion.sql`](dbt/tests/assert_local_time_conversion.sql) asserts three independent things about it — including that the offset is a whole number of hours between 1 and 23, so a conversion that silently no-ops fails the build.

That is the theme of the test suite generally: the failures worth guarding against are the ones that still produce a plausible number.

### Custom date ranges that cannot drift from the presets

The precomputed `kpi_*` models are built at `(period, key)` grain, which is exactly what makes an arbitrary window impossible — there is no row for "March 3rd to April 11th". Rather than move that aggregation into the dashboard, the same SQL is published as parameterized warehouse relations taking `(start, end)`.

That creates two ways to compute the same KPI, so [`assert_range_macros_match_periods`](dbt/tests/assert_range_macros_match_periods.sql) requires them to agree exactly on all five canonical windows. It caught 168 real mismatches the first time it ran, and it runs on every warehouse.

### A forecast that reports its own error

`kpi_revenue_forecast` holds out the most recent 28 days, fits on everything before them, predicts those days and publishes the weighted absolute percentage error — next to the same figure for a naive flat average. If the model isn't beating the baseline, the dashboard says so.

WAPE rather than MAPE, deliberately: MAPE divides by each day's actual, so one partially-extracted day with $24 of sales against a $3,400 forecast is a 14,000% error on its own. It dragged the reported figure to 464% while every ordinary day was within a few percent.

## Data privacy

This repository is public. The store's data is not, and the separation is enforced rather than promised.

- **Nothing derived from the real store is committed.** `data/` is Git-ignored except for `.gitkeep`; the Bronze layer is ~800MB of real business data that lives only on the owner's machine.
- **`.env` holds the Square token**, is Git-ignored, and is never logged or printed. [`scripts/security_check.py`](scripts/security_check.py) scans the tree for secret-shaped patterns and runs in CI on every push.
- **CI has no credentials at all.** It generates a synthetic fixture and runs the entire pipeline over it — Bronze → Silver → dbt build → dashboard smoke test → Dagster validation.
- **The hosted demo is synthetic**, generated at boot. It has no Square token and no path to one.
- **Development uses the Square Sandbox by default.** Reaching the real store requires an explicit `RETAILPULSE_ALLOW_PRODUCTION=1` per command, and every production call the project makes is read-only — see [`docs/production-switch.md`](docs/production-switch.md).

Full policy: [`docs/security-and-data-privacy.md`](docs/security-and-data-privacy.md).

## Business questions it answers

**Sales performance** — daily and weekly trends, best hours and days, average transaction value, items per basket, weekday vs. weekend, period-over-period comparisons.

**Product performance** — top sellers, category net sales, slow movers, trending items, per-item weekly history and projection.

**Payments and adjustments** — payment-method mix, revenue lost to discounts, Square processing fees, and an order-by-order reconciliation between recorded totals and money collected.

**Gross margin** *(optional)* — only where vendor costs are on file, with coverage reported. Profit is never fabricated from missing costs.

> Square sales data alone is *revenue*, not *profit*. Net sales is never described as profit in this project.

Two details that keep comparisons truthful: windows anchor on the **most recent day with sales**, not on today, so a stale extract doesn't manufacture a decline; and a comparison is **suppressed** rather than shown when the extract doesn't fully cover the earlier window, because a 90-day window against two days of prior history reads as +4,659%.

### Deliberately built but not surfaced

Inventory (`kpi_inventory_position`) and gross margin are implemented and tested but **absent from the dashboard**. Against the real store, 480 of 1,584 items carried negative on-hand counts — stock sold but never recorded as received — which made the reorder signal noise. Reinstating it needs the counts corrected at source. Shipping a confident-looking number built on data that doesn't support it is the failure mode worth avoiding.

## Running the real pipeline

See [`docs/setup-guide.md`](docs/setup-guide.md) for full instructions.

```bash
cp .env.example .env    # paste a Square Sandbox token — see docs/setup-guide.md
make install
make doctor             # diagnostics that never reveal the token
make extract-sandbox
make silver
make dbt-build          # 125 pass, 1 intentional warning
```

Gold tables land in `data/gold/warehouse.duckdb`. Open it with `duckdb data/gold/warehouse.duckdb` and query `main_marts.fact_order_line` or any `main_marts.kpi_*` model directly.

```bash
make test lint security-check   # 58 pytest, ruff, credential scan
make dagster-validate           # load every asset, check, job and schedule
```

### Other warehouses

```bash
python3 scripts/load_silver_to_snowflake.py     # or load_silver_to_bigquery.py
RETAILPULSE_DBT_TARGET=snowflake dbt build --project-dir dbt --profiles-dir dbt
```

The models are portable; the *ingestion* is not, which is why each cloud target has its own loader. `external_location` is a dbt-duckdb feature — every other adapter needs Silver physically loaded. That is the honest shape of a warehouse migration.

## Technologies

Python 3.11 · [httpx](https://www.python-httpx.org/) · [pydantic](https://docs.pydantic.dev/) + pydantic-settings · [DuckDB](https://duckdb.org/) · [dbt](https://www.getdbt.com/) (`dbt-duckdb`, `dbt-snowflake`, `dbt-bigquery`) · [Dagster](https://dagster.io/) + dagster-dbt · [Streamlit](https://streamlit.io/) + Altair · Docker · Terraform · Airflow (a parallel DAG, for comparison) · pytest + ruff · Square REST API `2026-07-15`

**Why DuckDB as the default:** it is a free, embedded, columnar OLAP engine, and reading Parquet directly off disk is the same pattern a lakehouse uses with S3 and Iceberg, minus the account. Anyone can clone this repo and get a working warehouse in one command — which is also why the Snowflake and BigQuery targets exist: to show the choice is not a constraint.

## What's next

- Schema contracts at the Bronze→Silver boundary — Pydantic at the edge, dbt `contract: enforced` on staging, source freshness
- Incremental `fact_order_line` with a lookback window, since Square can amend an order after close
- A second source: supplier invoices, which forces real entity resolution between invoice text and the Square catalog

See [`docs/project-charter.md`](docs/project-charter.md) for the charter, [`docs/data-dictionary.md`](docs/data-dictionary.md) for the model reference, and [`docs/orchestration.md`](docs/orchestration.md) for the asset graph.
