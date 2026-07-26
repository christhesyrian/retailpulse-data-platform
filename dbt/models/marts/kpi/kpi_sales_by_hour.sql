-- Sales by hour of day, one row per (period, hour), to surface peak trading
-- hours within the window the dashboard is showing.
--
-- Hours come from closed_at, which Square records in UTC — so these are UTC
-- hours, not store-local ones. The dashboard labels them as such.
with lines as (
    select
        cast(closed_at as date) as sale_date,
        cast(extract(hour from closed_at) as integer) as hour_of_day,
        square_order_id,
        net_sales_cents
    from {{ ref('fact_order_line') }}
    where closed_at is not null
),

periods as (
    select * from {{ ref('dim_period') }}
)

select
    p.period_label,
    p.period_order,
    l.hour_of_day,
    count(distinct l.square_order_id) as orders,
    sum(l.net_sales_cents) as net_sales_cents
from periods p
join lines l on l.sale_date between p.period_start and p.period_end
group by 1, 2, 3
order by p.period_order, l.hour_of_day
