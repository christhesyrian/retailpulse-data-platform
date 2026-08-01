-- Per-item, per-week sales history — the base time series for item trends
-- and the forecast. One row per (item, ISO week starting Monday).
--
-- Real stores have "custom" line items typed at the register with no catalog
-- link (null catalog_object_id). We still want to count those sales, so the
-- item key falls back to the typed name for them ('custom:<name>'); catalog
-- items key on their variation id as usual.
-- Grouping is on (week, item) only. Grouping on the display names too would
-- split one item into several rows whenever the same catalog id was rung up
-- under differently-typed names, which double-counts it in anything that
-- indexes the series by position (the forecast did exactly that).
select
    date_trunc('week', sale_date)::date as week_start,
    coalesce(catalog_object_id, 'custom:' || coalesce(item_name, 'unknown')) as variation_id,
    max(item_name) as item_name,
    max(variation_name) as variation_name,
    count(distinct square_order_id) as orders,
    sum(quantity) as units_sold,
    sum(net_sales_cents) as net_sales_cents
from {{ ref('fact_order_line') }}
where sale_date is not null
group by 1, 2
order by week_start, item_name
