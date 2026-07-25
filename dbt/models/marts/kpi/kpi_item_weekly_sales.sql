-- Per-item, per-week sales history — the base time series for item trends
-- and the forecast. One row per (item variation, ISO week starting Monday).
select
    date_trunc('week', closed_at)::date as week_start,
    catalog_object_id as variation_id,
    item_name,
    variation_name,
    count(distinct square_order_id) as orders,
    sum(quantity) as units_sold,
    sum(net_sales_cents) as net_sales_cents
from {{ ref('fact_order_line') }}
where closed_at is not null
group by 1, 2, 3, 4
order by week_start, item_name
