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
        sale_date,
        closed_at_local,
        square_order_id,
        -- same item key as the rest of the item KPIs: custom (non-catalog)
        -- register items fall back to their typed name.
        coalesce(catalog_object_id, 'custom:' || coalesce(item_name, 'unknown')) as variation_id
    from {{ ref('fact_order_line') }}
    where sale_date is not null
)

select
    min(sale_date) as first_sale_date,
    max(sale_date) as last_sale_date,
    max(closed_at_local) as last_order_at,
    {{ date_diff_in('day', 'min(sale_date)', 'max(sale_date)') }} + 1 as days_covered,
    count(distinct sale_date) as days_with_sales,
    count(distinct {{ date_trunc_to('week', 'sale_date') }}) as weeks_covered,
    -- complete ISO weeks only — what the forecast can actually fit on.
    -- Spelled as a conditional CASE rather than the SQL-standard aggregate
    -- FILTER clause, which DuckDB accepts and Snowflake has no support for.
    count(distinct case
        when {{ date_trunc_to('week', 'sale_date') }} < {{ date_trunc_to('week', 'current_date') }}
            then sale_date
    end) as days_in_complete_weeks,
    count(distinct case
        when {{ date_trunc_to('week', 'sale_date') }} < {{ date_trunc_to('week', 'current_date') }}
            then {{ date_trunc_to('week', 'sale_date') }}
    end) as complete_weeks_covered,
    count(distinct square_order_id) as orders,
    count(distinct variation_id) as items_sold,
    -- Dates are the store's own calendar days now, so this no longer goes
    -- negative from the UTC offset. The clamp stays because current_date is
    -- the machine's date, which can still sit a day behind a store trading
    -- east of it.
    greatest(0, {{ date_diff_in('day', 'max(sale_date)', 'current_date') }}) as days_since_last_sale
from lines
