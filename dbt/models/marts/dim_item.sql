-- NOTE: valid_from/valid_to are placeholder SCD2 columns matching
-- sql/warehouse_schema.sql's target shape; real history tracking (dbt
-- snapshots) is future work. is_current here is derived from Square's own
-- is_deleted flag rather than hardcoded, since we do track that signal.
--
-- category_name is NORMALIZED here so every downstream report rolls up
-- consistently: generic uppercase/trim/whitespace collapse (merges
-- 'Beer'/'BEER'), then the optional category_overrides map (merges typos and
-- synonyms, e.g. a mis-spelled 'BEVERGE' -> 'BEVERAGE'). category_name_raw
-- preserves the original for traceability. The Square catalog is never modified.
with items as (
    select * from {{ ref('stg_catalog_items') }}
),

overrides as (
    select raw_norm, canonical_category from {{ ref('stg_category_overrides') }}
)

select
    row_number() over (order by items.variation_id) as item_key,
    items.variation_id as square_catalog_object_id,
    items.item_name,
    items.variation_name,
    coalesce(o.canonical_category, {{ normalize_category('items.category_name') }})
        as category_name,
    items.category_name as category_name_raw,
    items.sku,
    items.price_cents as price_amount_cents,
    items.currency,
    current_timestamp as valid_from,
    cast(null as timestamp) as valid_to,
    not items.is_deleted as is_current
from items
left join overrides o
    on {{ normalize_category('items.category_name') }} = o.raw_norm
