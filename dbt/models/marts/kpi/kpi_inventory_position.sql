-- Current stock position per item, with a rough days-of-inventory estimate
-- and a reorder signal. Sales velocity is units sold over a trailing 30-day
-- window; days_of_inventory = on_hand / (units_sold_30d / 30).
--
-- This is a first-pass reorder heuristic, not the demand-forecast-driven
-- recommendation planned for M9 — thresholds are fixed, not learned.
with velocity as (
    select
        catalog_object_id,
        sum(quantity) as units_sold_30d
    from {{ ref('fact_order_line') }}
    where sale_date >= {{ add_days('current_date', -30) }}
    group by catalog_object_id
),

snapshot as (
    select
        square_catalog_object_id,
        item_key,
        quantity_on_hand,
        calculated_at
    from {{ ref('fact_inventory_snapshot') }}
)

select
    s.square_catalog_object_id,
    i.item_name,
    i.category_name,
    s.quantity_on_hand,
    coalesce(v.units_sold_30d, 0) as units_sold_30d,
    round(coalesce(v.units_sold_30d, 0) / 30.0, 3) as avg_daily_units,
    round(s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0), 1) as days_of_inventory,
    case
        when coalesce(v.units_sold_30d, 0) = 0 then 'no_recent_sales'
        when s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0) < 7 then 'reorder_soon'
        when s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0) < 14 then 'watch'
        else 'ok'
    end as stock_status,
    s.calculated_at
from snapshot s
left join {{ ref('dim_item') }} i on s.item_key = i.item_key
left join velocity v on s.square_catalog_object_id = v.catalog_object_id
order by days_of_inventory nulls last
