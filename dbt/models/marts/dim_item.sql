-- The item dimension as it stands today: one row per catalog item variation.
--
-- This is now the current slice of `dim_item_history`, which holds the full
-- Type 2 history. Everything that reads a fact table joins here, because a
-- fact carries `item_key` and this table has exactly one row per item_key —
-- joining straight to the history would fan a fact row out across every
-- version of its item.
--
-- The category normalisation and the lottery flag live in dim_item_history,
-- deliberately. They used to live here, and duplicating them across the two
-- would let a current-state report and a point-in-time report disagree about
-- what a category is.
--
-- valid_from / valid_to are real now. They used to be `current_timestamp` and
-- `null` — placeholder columns matching the shape in sql/warehouse_schema.sql
-- while claiming a history that did not exist. valid_from is the moment this
-- version of the item first appeared in a snapshot, and valid_to is null
-- because, by definition, this is the version that has not been superseded.
select
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
    valid_from,
    valid_to,
    -- `is_current` here means what it always meant: Square has not deleted
    -- this item. It is NOT the SCD2 sense of "latest version" — every row in
    -- this table is a latest version, so that flag would be a constant. The
    -- history table uses the name in the SCD2 sense; this is the reason the
    -- two are separate models rather than one with a filter.
    not is_deleted as is_current
from {{ ref('dim_item_history') }}
where is_current
