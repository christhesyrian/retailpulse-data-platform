-- Per-item, per-week sales history — the base time series for item trends
-- and the forecast. One row per (item, ISO week starting Monday).
--
-- Real stores have "custom" line items typed at the register with no catalog
-- link (null catalog_object_id). We still want to count those sales, so the
-- item key falls back to the typed name for them ('custom:<name>'); catalog
-- items key on their variation id as usual.
select
    date_trunc('week', closed_at)::date as week_start,
    coalesce(catalog_object_id, 'custom:' || coalesce(item_name, 'unknown')) as variation_id,
    item_name,
    variation_name,
    count(distinct square_order_id) as orders,
    sum(quantity) as units_sold,
    sum(net_sales_cents) as net_sales_cents
from {{ ref('fact_order_line') }}
where closed_at is not null
group by 1, 2, 3, 4
order by week_start, item_name
