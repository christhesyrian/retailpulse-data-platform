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
    -- A hash of the catalog id, NOT row_number().
    --
    -- row_number() here was a live data-corruption bug once fact_order_line
    -- became incremental. The rank is recomputed on every build, so adding a
    -- catalog item shifts the key of every item that sorts after it — while
    -- fact rows merged on earlier runs keep the number they were given. After
    -- one refresh that added 17 items, 135,534 of 153,314 fact rows pointed at
    -- the wrong item, and category totals were wrong by up to 5x (SCRATCHER
    -- reported $56k against an actual $286k).
    --
    -- Nothing failed. The `relationships` test passed the whole time, because
    -- the keys it checked all still existed in dim_item — they had simply
    -- stopped meaning the same thing. assert_fact_item_key_resolves is the
    -- test that actually catches this.
    {{ surrogate_key(['items.variation_id']) }} as item_key,
    items.variation_id as square_catalog_object_id,
    items.item_name,
    items.variation_name,
    coalesce(o.canonical_category, {{ normalize_category('items.category_name') }})
        as category_name,
    items.category_name as category_name_raw,
    -- Lottery and scratchers are always the top sellers by units, because the
    -- customer picks the store rather than the product and volume follows the
    -- jackpot. Ranking them alongside merchandise buries every item the owner
    -- could actually act on. The flag lives here, driven by the
    -- `lottery_categories` var, so "what counts as lottery" is one tested
    -- definition rather than a filter re-typed in each place that needs it.
    coalesce(o.canonical_category, {{ normalize_category('items.category_name') }})
        in ({{ "'" ~ var('lottery_categories') | join("', '") ~ "'" }}) as is_lottery,
    items.sku,
    items.price_cents as price_amount_cents,
    items.currency,
    current_timestamp as valid_from,
    cast(null as timestamp) as valid_to,
    not items.is_deleted as is_current
from items
left join overrides o
    on {{ normalize_category('items.category_name') }} = o.raw_norm
