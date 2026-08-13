-- Type 2 history for the item dimension: one row per item per version.
--
-- `dim_item` answers "what is this item?". This answers "what was this item
-- called, priced and categorised at the time something happened to it?" —
-- which is the question a Type 1 dimension cannot answer at all, because it
-- overwrites. Rename an item and last year's sales retroactively acquire the
-- new name.
--
-- Two keys, and the distinction is the entire point:
--
--   item_key          identifies the ITEM. Stable forever, one per catalog id.
--                     This is what the facts carry, so joining a fact to this
--                     table on item_key alone will fan out across versions —
--                     the join needs a validity predicate too. See
--                     assert_item_history_has_no_fanout.
--
--   item_version_key  identifies one VERSION of the item. Unique here.
--
-- Business logic (category normalisation, the lottery flag) lives here rather
-- than in dim_item, because dim_item is now a slice of this table and the two
-- must not be able to disagree about what a category is.
--
-- DO NOT `--full-refresh` the underlying snapshot. Every other object in this
-- project can be dropped and rebuilt from Bronze; this one cannot, because it
-- is the record of what changed and when. Rebuilding it collapses all history
-- to a single version per item, silently.
with versions as (
    select * from {{ ref('scd_catalog_items') }}
),

overrides as (
    select raw_norm, canonical_category from {{ ref('stg_category_overrides') }}
),

resolved as (
    select
        v.item_version_key,
        {{ surrogate_key(['v.variation_id']) }} as item_key,
        v.variation_id as square_catalog_object_id,
        v.item_name,
        v.variation_name,
        coalesce(o.canonical_category, {{ normalize_category('v.category_name') }})
            as category_name,
        v.category_name as category_name_raw,
        coalesce(o.canonical_category, {{ normalize_category('v.category_name') }})
            in ({{ "'" ~ var('lottery_categories') | join("', '") ~ "'" }}) as is_lottery,
        v.sku,
        v.price_cents as price_amount_cents,
        v.currency,
        v.is_deleted,
        v.valid_from,
        v.valid_to
    from versions v
    left join overrides o
        on {{ normalize_category('v.category_name') }} = o.raw_norm
)

select
    item_version_key,
    item_key,
    square_catalog_object_id,
    item_name,
    variation_name,
    category_name,
    category_name_raw,
    is_lottery,
    sku,
    price_amount_cents,
    currency,
    is_deleted,
    valid_from,
    valid_to,
    -- The open-ended version. dbt leaves valid_to null on the live row rather
    -- than writing a sentinel far-future date, so "current" is "not yet
    -- closed" rather than a comparison against 9999-12-31.
    valid_to is null as is_current,
    -- 1 for the first version of an item, 2 for the next, and so on. Cheap to
    -- compute here and much easier to read than a pair of timestamps when you
    -- are trying to work out how often something churns.
    cast(row_number() over (
        partition by item_key order by valid_from
    ) as integer) as version_number
from resolved
