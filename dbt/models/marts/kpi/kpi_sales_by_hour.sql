-- Sales by hour of day, one row per (period, hour), to surface peak trading
-- hours within the window the dashboard is showing.
--
-- Hours are the STORE's hours: closed_at_local is converted from Square's UTC
-- timestamp in fact_order_line using the location's own timezone. Reading the
-- hour straight off the UTC timestamp put this store's peak at 22:00–06:00,
-- which is just the evening trade shifted by the UTC offset.
with lines as (
    select
        sale_date,
        cast(extract(hour from closed_at_local) as integer) as hour_of_day,
        square_order_id,
        net_sales_cents
    from {{ ref('fact_order_line') }}
    where sale_date is not null
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
