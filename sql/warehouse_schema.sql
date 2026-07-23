-- RetailPulse target dimensional model. Implement in dbt after Bronze extraction is validated.

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
