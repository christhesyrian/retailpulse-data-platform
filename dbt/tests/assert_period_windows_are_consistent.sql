-- A period must never report more sales than the wider period containing it.
--
-- This is the cheap end-to-end check on the whole period mechanism: if a
-- window's bounds were built wrong (an off-by-one on period_start, a prior
-- window overlapping the current one), the nesting breaks and this catches it
-- without asserting any specific dollar figure.
with s as (
    select period_label, period_days, net_sales_cents, orders
    from {{ ref('kpi_summary') }}
),

nested as (
    select
        inner_p.period_label as inner_period,
        outer_p.period_label as outer_period,
        inner_p.net_sales_cents as inner_net_sales,
        outer_p.net_sales_cents as outer_net_sales,
        inner_p.orders as inner_orders,
        outer_p.orders as outer_orders
    from s inner_p
    join s outer_p
        on outer_p.period_days is null
       or (inner_p.period_days is not null and outer_p.period_days > inner_p.period_days)
)

select *
from nested
where inner_net_sales > outer_net_sales
   or inner_orders > outer_orders
