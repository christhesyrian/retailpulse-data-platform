# Data Dictionary

This documents the entities RetailPulse extracts and the target warehouse grain. It describes *field shapes and meaning*, not real values — no example below is drawn from actual store data.

## Bronze (raw Square API responses)

| Entity | Square endpoint | Contents |
|---|---|---|
| `locations` | `GET /v2/locations` | Store location(s): id, name, address, timezone, status |
| `catalog` | `GET /v2/catalog/list` | Items, item variations, and categories: id, name, SKU, price, variation-to-item relationships |
| `orders` | `POST /v2/orders/search` | Orders with nested line items: id, location, line items (name, quantity, gross/discount/tax amounts), state, timestamps |
| `payments` | `GET /v2/payments` | Payments: id, order id, amount, tender/source type, processing fee, status, timestamps |
| `inventory` | `POST /v2/inventory/counts/batch-retrieve` | Inventory counts: catalog object id, location id, state, quantity, calculated_at |

Bronze preserves the Square response verbatim inside a `payload` key, alongside non-destructive `metadata` (source, entity, page number, extraction timestamp, run id, environment). See [`architecture.md`](architecture.md) for the exact envelope shape.

## Reference input (operator-maintained, NOT from Square)

| Input | Location | Contents |
|---|---|---|
| `vendor_costs.csv` | `data/input/` (Git-ignored) | One row per variation: `variation_id`, `item_name`, `category_name`, `vendor_name`, `unit_cost_cents`. Real acquisition costs are business data and never committed; generate synthetic/template with `scripts/generate_synthetic_vendor_costs.py`. |
| `category_overrides.csv` | `data/input/` (Git-ignored) | Optional `raw_category,canonical_category` map to merge typo/synonym categories Square can't (e.g. a mis-spelled `BEVERGE`→`BEVERAGE`). Applied in `dim_item` on top of generic uppercase/trim normalization. Generate a starter with `scripts/generate_category_overrides.py`. |

## Silver (implemented — `data/silver/*.parquet`, the local lake, rebuilt from Bronze on every run)

| Table | Grain | Fields |
|---|---|---|
| `locations.parquet` | One row per location | `location_id`, `name`, `status`, `timezone`, `currency`, `country`, `business_name`, `merchant_id`, `created_at` |
| `catalog_items.parquet` | One row per catalog item variation | `variation_id`, `item_id`, `item_name`, `variation_name`, `category_id`, `category_name`, `price_cents` (BIGINT), `currency`, `sku`, `is_deleted` (BOOLEAN), `updated_at` |
| `order_lines.parquet` | One row per order line item | `order_id`, `line_item_uid`, `location_id`, `catalog_object_id`, `item_name`, `variation_name`, `quantity`, `gross_sales_cents` (BIGINT), `discount_cents` (BIGINT), `tax_cents` (BIGINT), `net_sales_cents` (BIGINT), `currency`, `order_state`, `order_created_at`, `order_updated_at`, `closed_at` |
| `payments.parquet` | One row per payment | `payment_id`, `order_id`, `location_id`, `amount_cents` (BIGINT), `currency`, `status`, `source_type`, `card_brand`, `card_last_4`, `processing_fee_cents` (BIGINT), `created_at`, `updated_at` |
| `inventory_snapshots.parquet` | One row per variation/location/state | `catalog_object_id`, `catalog_object_type`, `location_id`, `state`, `quantity` (DOUBLE), `calculated_at` |

`payments.parquet` intentionally excludes card `fingerprint` and `bin` — those are dropped during normalization rather than carried forward from Bronze. Timestamps and `quantity` are kept as text in Silver; dbt staging models cast them to real types (see below).

## Gold (implemented — `dbt/models/marts/`, materialized in `data/gold/warehouse.duckdb`)

Implements [`sql/warehouse_schema.sql`](../sql/warehouse_schema.sql)'s target grain via dbt-duckdb:

| Table | Grain | Key fields |
|---|---|---|
| `dim_location` | One row per Square location | `location_key` (surrogate), `square_location_id`, `location_name`, `timezone`, `valid_from/to`, `is_current` *(SCD2 columns are placeholders — see architecture.md)* |
| `dim_item` | One row per catalog item variation | `item_key` (surrogate), `square_catalog_object_id`, `item_name`, `variation_name`, `category_name`, `sku`, `price_amount_cents`, `currency`, `valid_from/to`, `is_current` *(derived from Square's `is_deleted`)* |
| `fact_order_line` | One row per order line item | `order_line_key` (surrogate), `square_order_id`, `square_line_item_uid`, `location_key`, `item_key`, `closed_at`, `quantity`, `gross_sales_cents`, `discount_cents`, `tax_cents`, `net_sales_cents` |
| `fact_payment` | One row per payment | `square_payment_id` (primary key), `square_order_id`, `location_key`, `created_at`, `updated_at`, `status`, `source_type`, `amount_cents`, `processing_fee_cents`, `currency` |
| `dim_date` | One row per calendar date | `date_day`, `date_key`, `year`, `month`, `month_name`, `day_of_month`, `day_of_week` (1=Mon..7=Sun), `day_name`, `week_of_year`, `is_weekend` |
| `dim_vendor` | One row per vendor | `vendor_key` (surrogate), `vendor_name`, `variation_count` |
| `fact_inventory_snapshot` | One row per variation/location/snapshot time | `inventory_snapshot_key` (surrogate), `square_catalog_object_id`, `item_key`, `location_key`, `square_location_id`, `state`, `quantity_on_hand`, `calculated_at` |
| `fact_order_line_margin` | One row per order line | `order_line_key`, `item_key`, `vendor_key`, `category_name`, `vendor_name`, `quantity`, `net_sales_cents`, `unit_cost_cents`, `cogs_cents`, `gross_profit_cents`, `gross_margin_pct`, `has_cost` |
| `dim_category` *(future)* | One row per category | — |
| `fact_refund` *(future)* | One row per refund | — |

Every surrogate key and natural key has a dbt `not_null`/`unique` test; every fact-to-dimension foreign key has a `relationships` test. See `dbt/models/marts/_marts.yml`.

## KPI models (implemented — `dbt/models/marts/kpi/`)

The metric layer, sourced only from the facts/dimensions above. Consumed by the Streamlit dashboard.

| Model | Grain | Notes |
|---|---|---|
| `kpi_summary` | One row (overall) | Net sales, orders, AOV, units/order, discounts, tax, processing fees, total collected |
| `kpi_daily_sales` | One row per calendar day | Left-joined from `dim_date`; zero-sales days appear as 0 |
| `kpi_sales_by_category` | One row per category | Net sales, units, and share of total |
| `kpi_sales_by_weekday` | One row per ISO weekday (1–7) | Net sales, orders, AOV |
| `kpi_sales_by_hour` | One row per hour (0–23) | Net sales, orders |
| `kpi_payment_methods` | One row per tender type | Amount collected, processing fees, share of total |
| `rpt_order_payment_reconciliation` | One row per order id | Order total vs. paid, `variance_cents`, `reconciliation_status` |
| `kpi_item_sales` | One row per item variation | `item_name`, `category_name`, units this/last week, this/last month, `avg_weekly_units_4wk`, `wow_trend_pct`, `units_total`, first/last sold |
| `kpi_item_weekly_sales` | One row per item per ISO week | `week_start`, `variation_id`, `item_name`, `units_sold`, `orders`, `net_sales_cents` |
| `kpi_item_forecast` | One row per item per future week (next 4) | `forecast_week_start`, `weeks_ahead`, `forecast_units`, `method` (linear_trend / avg_fallback), `weeks_of_history` |
| `kpi_margin_by_category` | One row per category | Net sales, COGS, gross profit, margin %, cost coverage % (costed lines only; empty without vendor costs) |
| `kpi_margin_by_vendor` | One row per vendor | Net sales, COGS, gross profit, margin % (empty without vendor costs) |
| `kpi_inventory_position` | One row per variation | On-hand, units sold (30d), days of inventory, `stock_status` (empty without inventory tracking) |

## Conventions

- Money is stored as integer cents until the presentation layer, matching Square's own representation.
- **Net sales = gross − discount, EXCLUDING tax.** Tax is a separate column; the tax-inclusive amount collected is `net_sales_cents + tax_cents`, which is what the payment reconciliation checks against `fact_payment.amount_cents`.
- All Square-issued IDs (`square_order_id`, `square_payment_id`, `square_catalog_object_id`, `square_location_id`) are preserved for lineage and deduplication.
- Timestamps are UTC.
- Net sales / revenue fields are never labeled or described as profit — profit requires vendor cost data, which is out of scope until M5.
