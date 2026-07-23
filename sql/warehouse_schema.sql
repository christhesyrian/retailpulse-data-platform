-- RetailPulse target dimensional model. Implemented as dbt models in
-- dbt/models/marts/ (dim_location, dim_item, fact_order_line, fact_payment).
-- This file remains the reference DDL/grain definition; dbt/models/marts/
-- is the source of truth for actual column/type decisions, which may
-- differ slightly (e.g. surrogate keys generated via row_number()).
-- dim_category, dim_date, fact_refund, and fact_inventory_snapshot below
-- are not yet implemented in dbt.

create table if not exists dim_location (
    location_key bigint generated always as identity primary key,
    square_location_id text unique not null,
    location_name text,
    timezone text,
    valid_from timestamp,
    valid_to timestamp,
    is_current boolean default true
);

create table if not exists dim_item (
    item_key bigint generated always as identity primary key,
    square_catalog_object_id text unique not null,
    item_name text,
    variation_name text,
    category_name text,
    sku text,
    price_amount_cents bigint,
    currency text,
    valid_from timestamp,
    valid_to timestamp,
    is_current boolean default true
);

create table if not exists fact_order_line (
    order_line_key bigint generated always as identity primary key,
    square_order_id text not null,
    square_line_item_uid text not null,
    location_key bigint references dim_location(location_key),
    item_key bigint references dim_item(item_key),
    closed_at timestamp,
    quantity numeric(18, 4),
    gross_sales_cents bigint,
    discount_cents bigint,
    tax_cents bigint,
    net_sales_cents bigint,
    unique (square_order_id, square_line_item_uid)
);

create table if not exists fact_payment (
    square_payment_id text primary key,
    square_order_id text,
    location_key bigint references dim_location(location_key),
    created_at timestamp,
    updated_at timestamp,
    status text,
    source_type text,
    amount_cents bigint,
    processing_fee_cents bigint,
    currency text
);

-- M5: inventory + vendor costs (implemented in dbt/models/marts/).

create table if not exists dim_vendor (
    vendor_key bigint generated always as identity primary key,
    vendor_name text unique not null,
    variation_count bigint
);

create table if not exists fact_inventory_snapshot (
    inventory_snapshot_key bigint generated always as identity primary key,
    square_catalog_object_id text not null,
    item_key bigint references dim_item(item_key),
    location_key bigint references dim_location(location_key),
    square_location_id text,
    state text,
    quantity_on_hand numeric(18, 4),
    calculated_at timestamp
);

-- Vendor/acquisition costs come from an operator-maintained input
-- (data/input/vendor_costs.csv), NOT from Square. fact_order_line_margin
-- joins them to fact_order_line to compute COGS and gross profit:
--   gross_profit_cents = net_sales_cents - (unit_cost_cents * quantity)
-- Lines with no cost on file have NULL cogs/gross_profit (has_cost = false).
create table if not exists fact_order_line_margin (
    order_line_key bigint primary key references fact_order_line(order_line_key),
    square_order_id text,
    square_line_item_uid text,
    item_key bigint references dim_item(item_key),
    vendor_key bigint references dim_vendor(vendor_key),
    category_name text,
    vendor_name text,
    quantity numeric(18, 4),
    net_sales_cents bigint,
    unit_cost_cents bigint,
    cogs_cents bigint,
    gross_profit_cents bigint,
    gross_margin_pct numeric(8, 2),
    has_cost boolean
);
