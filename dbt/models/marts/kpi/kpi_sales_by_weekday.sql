-- Sales aggregated by day of week (Monday..Sunday) to surface weekday vs.
-- weekend patterns.
with lines as (
    select
        cast(closed_at as date) as sale_date,
        square_order_id,
        net_sales_cents
    from {{ ref('fact_order_line') }}
    where closed_at is not null
)

select
    isodow(sale_date) as day_of_week,        -- 1 = Monday ... 7 = Sunday
    dayname(sale_date) as day_name,
    isodow(sale_date) in (6, 7) as is_weekend,
    count(distinct square_order_id) as orders,
    sum(net_sales_cents) as net_sales_cents,
    case
        when count(distinct square_order_id) > 0
            then round(sum(net_sales_cents) * 1.0 / count(distinct square_order_id))
        else 0
    end as avg_order_value_cents
from lines
group by 1, 2, 3
order by day_of_week
