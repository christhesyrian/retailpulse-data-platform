-- NOTE: valid_from/valid_to are placeholder SCD2 columns matching
-- sql/warehouse_schema.sql's target shape; real history tracking (dbt
-- snapshots) is future work. is_current here is derived from Square's own
-- is_deleted flag rather than hardcoded, since we do track that signal.
select
    row_number() over (order by variation_id) as item_key,
    variation_id as square_catalog_object_id,
    item_name,
    variation_name,
    category_name,
    sku,
    price_cents as price_amount_cents,
    currency,
    current_timestamp as valid_from,
    cast(null as timestamp) as valid_to,
    not is_deleted as is_current
from {{ ref('stg_catalog_items') }}
