-- Sales by hour of day, to surface peak trading hours.
select
    cast(extract(hour from closed_at) as integer) as hour_of_day,
    count(distinct square_order_id) as orders,
    sum(net_sales_cents) as net_sales_cents
from {{ ref('fact_order_line') }}
where closed_at is not null
group by 1
order by hour_of_day
