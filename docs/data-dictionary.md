# Data Dictionary

This documents the entities RetailPulse extracts and the target warehouse grain. It describes *field shapes and meaning*, not real values — no example below is drawn from actual store data.

## Bronze (raw Square API responses)

| Entity | Square endpoint | Contents |
|---|---|---|
| `locations` | `GET /v2/locations` | Store location(s): id, name, address, timezone, status |
| `catalog` | `GET /v2/catalog/list` | Items, item variations, and categories: id, name, SKU, price, variation-to-item relationships |
| `orders` | `POST /v2/orders/search` | Orders with nested line items: id, location, line items (name, quantity, gross/discount/tax amounts), state, timestamps |
| `payments` | `GET /v2/payments` | Payments: id, order id, amount, tender/source type, processing fee, status, timestamps |

Bronze preserves the Square response verbatim inside a `payload` key, alongside non-destructive `metadata` (source, entity, page number, extraction timestamp, run id, environment). See [`architecture.md`](architecture.md) for the exact envelope shape.

## Silver (implemented — `data/silver/*.csv`, rebuilt from Bronze on every run)

| Table | Grain | Fields |
|---|---|---|
| `locations.csv` | One row per location | `location_id`, `name`, `status`, `timezone`, `currency`, `country`, `business_name`, `merchant_id`, `created_at` |
| `catalog_items.csv` | One row per catalog item variation | `variation_id`, `item_id`, `item_name`, `variation_name`, `category_id`, `category_name`, `price_cents`, `currency`, `sku`, `is_deleted`, `updated_at` |
| `order_lines.csv` | One row per order line item | `order_id`, `line_item_uid`, `location_id`, `catalog_object_id`, `item_name`, `variation_name`, `quantity`, `gross_sales_cents`, `discount_cents`, `tax_cents`, `net_sales_cents`, `currency`, `order_state`, `order_created_at`, `order_updated_at`, `closed_at` |
| `payments.csv` | One row per payment | `payment_id`, `order_id`, `location_id`, `amount_cents`, `currency`, `status`, `source_type`, `card_brand`, `card_last_4`, `processing_fee_cents`, `created_at`, `updated_at` |

`payments.csv` intentionally excludes card `fingerprint` and `bin` — those are dropped during normalization rather than carried forward from Bronze.

## Target Gold warehouse grain

Defined in [`sql/warehouse_schema.sql`](../sql/warehouse_schema.sql) (not yet implemented — planned for M3):

| Table | Grain | Key fields |
|---|---|---|
| `dim_location` | One row per Square location (SCD2) | `square_location_id`, `location_name`, `timezone`, `valid_from/to`, `is_current` |
| `dim_item` | One row per catalog item variation (SCD2) | `square_catalog_object_id`, `item_name`, `variation_name`, `category_name`, `sku`, `price_amount_cents`, `currency`, `valid_from/to`, `is_current` |
| `fact_order_line` | One row per order line item | `square_order_id`, `square_line_item_uid`, `location_key`, `item_key`, `closed_at`, `quantity`, `gross_sales_cents`, `discount_cents`, `tax_cents`, `net_sales_cents` |
| `fact_payment` | One row per payment | `square_payment_id`, `square_order_id`, `location_key`, `created_at`, `updated_at`, `status`, `source_type`, `amount_cents`, `processing_fee_cents`, `currency` |
| `dim_category` *(future)* | One row per category | — |
| `dim_date` *(future)* | One row per calendar date | — |
| `fact_refund` *(future)* | One row per refund | — |
| `fact_inventory_snapshot` *(future)* | One row per item variation, location, and snapshot time | — |

## Conventions

- Money is stored as integer cents until the presentation layer, matching Square's own representation.
- All Square-issued IDs (`square_order_id`, `square_payment_id`, `square_catalog_object_id`, `square_location_id`) are preserved for lineage and deduplication.
- Timestamps are UTC.
- Net sales / revenue fields are never labeled or described as profit — profit requires vendor cost data, which is out of scope until M5.
