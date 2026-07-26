-- Single-row answer to "what window am I looking at, and how fresh is it?".
--
-- The dashboard shows a coverage banner (date range, days, weeks, how stale
-- the last sale is). That's a set of figures like any other, so it belongs in
-- a tested model rather than being derived in the presentation layer.
--
-- Note this describes the data that was EXTRACTED, not the store's whole
-- history: pulling a wider window (retailpulse extract-all --days N) moves
-- first_sale_date back. days_since_last_sale is the freshness signal — a
-- value above 1 usually means the extract hasn't been re-run today.
with lines as (
    select
        cast(closed_at as date) as sale_date,
        closed_at,
        square_order_id,
        -- same item key as the rest of the item KPIs: custom (non-catalog)
        -- register items fall back to their typed name.
        coalesce(catalog_object_id, 'custom:' || coalesce(item_name, 'unknown')) as variation_id
    from {{ ref('fact_order_line') }}
    where closed_at is not null
)

select
    min(sale_date) as first_sale_date,
    max(sale_date) as last_sale_date,
    max(closed_at) as last_order_at,
    date_diff('day', min(sale_date), max(sale_date)) + 1 as days_covered,
    count(distinct sale_date) as days_with_sales,
    count(distinct date_trunc('week', sale_date)) as weeks_covered,
    -- complete ISO weeks only — what the forecast can actually fit on.
    count(distinct sale_date) filter (
        where date_trunc('week', sale_date) < date_trunc('week', current_date)
    ) as days_in_complete_weeks,
    count(distinct case
        when date_trunc('week', sale_date) < date_trunc('week', current_date)
            then date_trunc('week', sale_date)
    end) as complete_weeks_covered,
    count(distinct square_order_id) as orders,
    count(distinct variation_id) as items_sold,
    -- Sale dates come from closed_at in UTC, so an evening sale in a US store
    -- lands on tomorrow's UTC date and the raw difference can go negative.
    -- Clamp at 0: "sold today or later" is simply fresh.
    greatest(0, date_diff('day', max(sale_date), current_date)) as days_since_last_sale
from lines
