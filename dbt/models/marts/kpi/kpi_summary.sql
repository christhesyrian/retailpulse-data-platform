-- Single-row headline KPIs for the top of the dashboard.
-- net_sales excludes tax; collected_cents is what actually changed hands
-- (net + tax), so collected should equal net_sales + tax (reconciliation).
with ol as (
    select
        count(distinct square_order_id) as orders,
        sum(quantity) as units,
        sum(gross_sales_cents) as gross_sales_cents,
        sum(discount_cents) as discount_cents,
        sum(tax_cents) as tax_cents,
        sum(net_sales_cents) as net_sales_cents
    from {{ ref('fact_order_line') }}
),

pay as (
    select
        sum(amount_cents) as collected_cents,
        sum(processing_fee_cents) as processing_fee_cents
    from {{ ref('fact_payment') }}
)

select
    ol.orders,
    ol.units,
    ol.gross_sales_cents,
    ol.discount_cents,
    ol.tax_cents,
    ol.net_sales_cents,
    case when ol.orders > 0 then round(ol.net_sales_cents * 1.0 / ol.orders) else 0 end
        as avg_order_value_cents,
    case when ol.orders > 0 then round(ol.units * 1.0 / ol.orders, 2) else 0 end
        as units_per_order,
    pay.collected_cents,
    pay.processing_fee_cents
from ol
cross join pay
