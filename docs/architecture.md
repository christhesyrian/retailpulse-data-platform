# Architecture

```mermaid
flowchart LR
    A[Square APIs] --> B[Python Extraction]
    V[Vendor costs CSV] --> E
    B --> C[Bronze Raw JSON]
    C --> D[Silver Parquet lake]
    D --> E[Gold facts & dimensions]
    E --> K[KPI + margin + inventory models]
    K --> F[Streamlit dashboard]
    K -.-> G[Forecasting - future]
```

## Layers

### Square APIs (implemented, read-only)

`src/retailpulse/square_client.py` calls read-only Square REST endpoints:

- `GET /v2/locations`
- `GET /v2/catalog/list`
- `POST /v2/orders/search`
- `GET /v2/payments`
- `POST /v2/inventory/counts/batch-retrieve` (current endpoint; the older `/v2/inventory/batch-retrieve-counts` is deprecated)

Requests carry `Authorization: Bearer <token>`, `Square-Version`, and a descriptive `User-Agent`. Transient failures (HTTP 429/500/502/503/504, and transport-level errors) are retried up to 5 attempts with exponential backoff and jitter, honoring `Retry-After` when Square sends it. Authentication and validation errors (4xx other than 429) fail immediately without retry, since retrying them cannot succeed.

**Production safeguard:** `SQUARE_ENVIRONMENT` defaults to `sandbox`. Selecting `production` is not enough on its own — commands that contact Square refuse to run unless `RETAILPULSE_ALLOW_PRODUCTION=1` is also set for that invocation (`require_production_opt_in()` in the CLI, exit code 3 otherwise). All operations are read-only regardless; this is a blast-radius guard so a leftover env value can't silently hit the real store. See [`production-switch.md`](production-switch.md).

### Python Extraction (implemented)

`src/retailpulse/extract/jobs.py` orchestrates one job per entity (locations, catalog, orders, payments, inventory), each following Square's cursor pagination until no `cursor` is returned, writing one raw page per API response.

### Bronze Raw JSON (implemented)

`src/retailpulse/storage.py` writes each page as an immutable, metadata-wrapped JSON file:

```text
data/bronze/square/<entity>/extracted_date=YYYY-MM-DD/page-00001-<timestamp>-<run_id>.json
```

```json
{
  "metadata": {
    "source": "square",
    "entity": "orders",
    "page_number": 1,
    "extracted_at": "2026-07-22T12:00:00+00:00",
    "run_id": "a1b2c3d4...",
    "environment": "sandbox"
  },
  "payload": { "...": "raw Square API response, unmodified" }
}
```

The filename embeds a per-run identifier, so two extraction runs never collide even within the same partition, and `write_raw_page` refuses to overwrite an existing path (`FileExistsError`). This directory is Git-ignored; it never leaves the local machine.

### Silver Normalized Tables (implemented)

`src/retailpulse/transform/silver.py` reads every Bronze JSON file for an entity (across every extraction run), deduplicates by the Square object's own `id`, and writes flattened, typed **Parquet** files to `data/silver/` — this directory is the project's local "data lake": columnar files on disk, engine-agnostic, exactly the format real lakehouses (S3 + Parquet/Delta/Iceberg) use, just without the object-storage layer. Parquet is written via an in-memory DuckDB table (`CREATE TABLE` + `COPY ... TO ... (FORMAT PARQUET)`), so no separate Arrow/pandas dependency is needed.

- **`locations.parquet`** — one row per location.
- **`catalog_items.parquet`** — one row per catalog item *variation*, joined against its parent `ITEM` and `CATEGORY` objects (Square's Catalog API returns these as separate flat objects; Silver resolves the `item_id`/`category_id` links). `category_name` is `null` when an item has no category assigned — this is reported honestly rather than guessed.
- **`order_lines.parquet`** — one row per order line item, flattening Square's nested `order.line_items[]` array. Money fields (`gross_sales_cents`, `discount_cents`, `tax_cents`, `net_sales_cents`) come straight from the line item's own computed totals, typed as `BIGINT`.
- **`payments.parquet`** — one row per payment. Card details are **minimized**: only `card_brand` and `card_last_4` are kept; `fingerprint` and `bin` from the raw Bronze payload are dropped, since Silver is the layer Gold and future exports read from.

Money columns are `BIGINT`, `is_deleted` is `BOOLEAN`; timestamps and `quantity` stay `VARCHAR` in Silver and are cast to real types in the dbt staging layer (see below) — keeping the type-casting decision visible and testable in SQL rather than silently baked into Python.

**Dedup rule:** for each Square object type, the record's own `updated_at` field (when present) determines which extraction run's snapshot wins, falling back to the Bronze `extracted_at` timestamp otherwise (locations don't carry their own `updated_at`). Since the same order/payment/catalog object is re-extracted on every run within its lookback window, this collapses N snapshots down to 1 current row per object — Silver is a rebuild, not an append: each `make silver` run regenerates the Parquet files from scratch from whatever Bronze currently holds.

Run it with `make silver` or `retailpulse transform-silver`.

### Gold Fact and Dimension Models (implemented)

`dbt/` is a `dbt-duckdb` project. DuckDB is a free, embedded, columnar OLAP database — no server, no account — and reads Parquet natively, so it plays the role of both "the lake's query engine" and "the warehouse" here.

- **Sources** (`dbt/models/staging/_sources.yml`) point directly at the Silver Parquet files using dbt-duckdb's `external_location` pattern (`read_parquet('.../{name}.parquet')`) — no data is copied anywhere until a model materializes it.
- **Staging models** (`dbt/models/staging/stg_*.sql`, materialized as views) cast Silver's `VARCHAR` timestamps and `quantity` to real `TIMESTAMP`/`DECIMAL` types using `try_cast` (bad data becomes `NULL`, not a crash).
- **Mart models** (`dbt/models/marts/*.sql`, materialized as tables in `data/gold/warehouse.duckdb`) implement `sql/warehouse_schema.sql`'s target grain: `dim_location`, `dim_item`, `fact_order_line`, `fact_payment`. Surrogate keys are generated with `row_number()` — small-scale and simple, appropriate for this data volume.
- **24 dbt schema tests** cover not-null and uniqueness on every primary/surrogate key, plus `relationships` tests verifying every fact row's `location_key`/`item_key` actually resolves to a dimension row (nulls are allowed where source data doesn't have a matching catalog object, e.g. orphaned line items from a deleted catalog item).

**Known limitation:** `dim_location`/`dim_item`'s `valid_from`/`valid_to`/`is_current` columns are placeholders matching `sql/warehouse_schema.sql`'s target shape — real SCD2 history tracking (dbt snapshots) isn't implemented yet; every row reflects only the current Silver snapshot. `fact_refund` and `fact_inventory_snapshot` from the original schema are also not yet built — deferred to later milestones (refunds/inventory need Square endpoints RetailPulse doesn't extract yet).

Run it with `make dbt-build` (runs models + all tests) or `make dbt-docs` (generates dbt's documentation site).

### KPI models and reconciliation (implemented)

`dbt/models/marts/dim_date.sql` is a calendar dimension built from a generated date spine over the span of order activity, so days with zero sales still appear (a gap day reads as 0, not a missing row).

`dbt/models/marts/kpi/` holds the metric layer — the KPI *definitions* live here as tested, version-controlled SQL rather than being buried in the dashboard:

- `kpi_summary` — single-row headline KPIs (net sales, orders, AOV, units/order, discounts, tax, fees, total collected).
- `kpi_daily_sales` — per-day sales (left-joined from `dim_date` so zero days appear).
- `kpi_sales_by_category`, `kpi_sales_by_weekday`, `kpi_sales_by_hour` — sales cut by product category, day of week, and hour of day.
- `kpi_payment_methods` — amount collected and Square processing fees by tender type.

A key semantic: **net sales = gross − discount, excluding tax** (fixed in Silver during M4). Tax is tracked separately; the tax-inclusive amount collected is `net_sales + tax`. Net sales is never described as profit — profit needs vendor cost data (added in M5, below).

`rpt_order_payment_reconciliation` full-outer-joins order-level totals (`net_sales + tax`) against payment totals per `order_id`. `tests/assert_order_payment_reconciled.sql` fails the build if any order and payment both exist but disagree (`mismatch`). Orphaned orders/payments (one side missing — expected in the Sandbox after re-seeding) are surfaced but not failed on.

**Internal vs. external reconciliation:** this is *internal* reconciliation — the pipeline agreeing with itself, which catches transformation bugs. Reconciling RetailPulse's totals against Square's own **Reporting API / Dashboard** figures (proving the extraction captured everything Square recorded) is a separate, stronger check that is deferred: the Reporting API is unreliable/limited in Sandbox, so a meaningful external reconciliation needs Production, which is out of scope for these milestones.

### Vendor costs, gross margin, and inventory (M5, implemented)

**Inventory.** `extract_inventory` pulls current IN_STOCK counts (read-only). Silver dedupes them per `(variation, location, state)` by `calculated_at` → `inventory_snapshots.parquet` → `fact_inventory_snapshot` (grained one row per variation/location/snapshot time). `kpi_inventory_position` joins on-hand to trailing-30-day sales velocity to estimate days-of-inventory and a fixed-threshold reorder signal (`reorder_soon` / `watch` / `ok` / `no_recent_sales`). The learned, forecast-driven version of this is deferred to M9.

**Vendor costs.** Acquisition costs are **not** in Square — they are operator-maintained reference data at `data/input/vendor_costs.csv` (Git-ignored; real cost data is business-sensitive and never committed). dbt reads the CSV directly as a `reference` source via `read_csv`; `scripts/generate_synthetic_vendor_costs.py` builds it from the Silver catalog (synthetic costs for demo/CI, or a fill-in template against a real catalog). `dim_vendor` is derived from it.

**Gross margin — the move from revenue to profit.** `fact_order_line_margin` left-joins order lines to vendor costs and computes `cogs = unit_cost × quantity` and `gross_profit = net_sales − cogs`, with `gross_margin_pct`. Cost coverage is explicit: when a line's variation has no cost on file, `cogs`/`gross_profit`/`gross_margin_pct` are **NULL** (not zero) and `has_cost` is false. The margin KPIs (`kpi_margin_by_category`, `kpi_margin_by_vendor`, and the margin fields in `kpi_summary`) aggregate only costed lines and report `cost_coverage_pct`, so a partially-costed catalog understates coverage rather than silently inflating margin. This is the first layer where the project reports **profit**; it still requires the operator's cost data and is never derived from Square alone.

### Streamlit dashboard (implemented)

`dashboard/app.py` reads `data/gold/warehouse.duckdb` **read-only** and renders the KPI models: headline tiles (including COGS / gross profit / margin / cost coverage), a daily-sales trend, category/weekday/hour breakdowns, payment-method mix, a live reconciliation status banner, gross-profit-by-category, and an inventory-position table with reorder flags. It contains no business SQL of its own — every figure is `select … from main_marts.kpi_*` — so the dashboard and the tested warehouse cannot drift apart. Streamlit is an optional (`dashboard`) dependency, kept out of the CI/pipeline dependency set; `scripts/smoke_dashboard_queries.py` guards the dashboard↔warehouse contract in CI without installing Streamlit.

Run it with `make demo-data` (build the warehouse from synthetic data) then `make dashboard`.

### Forecasting — not implemented (future work)

Demand forecasting and reorder recommendations, on top of the KPI/Gold layer.

## Configuration and secrets

`src/retailpulse/config.py` loads settings via `pydantic-settings`, typing the access token as `SecretStr` so it can't leak into a `repr()`, log line, or exception message. `SQUARE_ENVIRONMENT` defaults to `sandbox`; selecting `production` triggers a visible CLI warning before any request is made. See [`security-and-data-privacy.md`](security-and-data-privacy.md) for the full policy.
