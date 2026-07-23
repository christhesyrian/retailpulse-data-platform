-- Gross-margin enrichment of fact_order_line, joined to operator-maintained
-- vendor costs. This is where the pipeline finally computes PROFIT (not just
-- revenue): gross_profit = net_sales - COGS.
--
-- Cost coverage is honest: when a line's variation has no vendor cost on
-- file, cogs / gross_profit / gross_margin_pct are NULL (not zero) and
-- has_cost is false. Margin KPIs only aggregate rows where has_cost is true,
-- so missing costs understate coverage rather than silently inflating margin.
with order_lines as (
    select
        order_line_key,
        square_order_id,
        square_line_item_uid,
        item_key,
        catalog_object_id,
        quantity,
        net_sales_cents
    from {{ ref('fact_order_line') }}
),

costs as (
    select variation_id, vendor_name, unit_cost_cents from {{ ref('stg_vendor_costs') }}
),

items as (
    select item_key, item_name, category_name from {{ ref('dim_item') }}
),

vendors as (
    select vendor_key, vendor_name from {{ ref('dim_vendor') }}
)

select
    ol.order_line_key,
    ol.square_order_id,
    ol.square_line_item_uid,
    ol.item_key,
    v.vendor_key,
    i.category_name,
    c.vendor_name,
    ol.quantity,
    ol.net_sales_cents,
    c.unit_cost_cents,
    cast(round(c.unit_cost_cents * ol.quantity) as bigint) as cogs_cents,
    cast(ol.net_sales_cents - round(c.unit_cost_cents * ol.quantity) as bigint)
        as gross_profit_cents,
    case
        when c.unit_cost_cents is not null and ol.net_sales_cents > 0
            then round(
                100.0 * (ol.net_sales_cents - c.unit_cost_cents * ol.quantity)
                / ol.net_sales_cents, 2
            )
    end as gross_margin_pct,
    c.unit_cost_cents is not null as has_cost
from order_lines ol
left join costs c on ol.catalog_object_id = c.variation_id
left join items i on ol.item_key = i.item_key
left join vendors v on c.vendor_name = v.vendor_name
