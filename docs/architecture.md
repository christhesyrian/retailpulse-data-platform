# Architecture

```mermaid
flowchart LR
    A[Square APIs] --> B[Python Extraction]
    B --> C[Bronze Raw JSON]
    C --> D[Silver Normalized Tables]
    D --> E[Gold Fact and Dimension Models]
    E --> F[Dashboards and Forecasting]
```

## Layers

### Square APIs (implemented, read-only)

`src/retailpulse/square_client.py` calls four read-only Square REST endpoints:

- `GET /v2/locations`
- `GET /v2/catalog/list`
- `POST /v2/orders/search`
- `GET /v2/payments`

Requests carry `Authorization: Bearer <token>`, `Square-Version`, and a descriptive `User-Agent`. Transient failures (HTTP 429/500/502/503/504, and transport-level errors) are retried up to 5 attempts with exponential backoff and jitter, honoring `Retry-After` when Square sends it. Authentication and validation errors (4xx other than 429) fail immediately without retry, since retrying them cannot succeed.

### Python Extraction (implemented)

`src/retailpulse/extract/jobs.py` orchestrates one job per entity (locations, catalog, orders, payments), each following Square's cursor pagination until no `cursor` is returned, writing one raw page per API response.

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

**Known limitation:** `dim_location`/`dim_item`'s `valid_from`/`valid_to`/`is_current` columns are placeholders matching `sql/warehouse_schema.sql`'s target shape — real SCD2 history tracking (dbt snapshots) isn't implemented yet; every row reflects only the current Silver snapshot. `fact_refund`, `fact_inventory_snapshot`, `dim_category`, and `dim_date` from the original schema are also not yet built — deferred to later milestones (refunds/inventory need Square endpoints RetailPulse doesn't extract yet).

Run it with `make dbt-build` (runs models + all tests) or `make dbt-docs` (generates dbt's documentation site).

### Dashboards and Forecasting — not implemented (future work)

KPI dashboard reconciled against Square's own reporting, followed by demand forecasting and reorder recommendations.

## Configuration and secrets

`src/retailpulse/config.py` loads settings via `pydantic-settings`, typing the access token as `SecretStr` so it can't leak into a `repr()`, log line, or exception message. `SQUARE_ENVIRONMENT` defaults to `sandbox`; selecting `production` triggers a visible CLI warning before any request is made. See [`security-and-data-privacy.md`](security-and-data-privacy.md) for the full policy.
