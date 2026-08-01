-- Sales aggregated by day of week (Monday..Sunday), one row per
-- (period, weekday), to surface weekday vs. weekend patterns within the
-- window the dashboard is showing.
with lines as (
    select
        sale_date,
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
    isodow(l.sale_date) as day_of_week,        -- 1 = Monday ... 7 = Sunday
    dayname(l.sale_date) as day_name,
    isodow(l.sale_date) in (6, 7) as is_weekend,
    count(distinct l.square_order_id) as orders,
    sum(l.net_sales_cents) as net_sales_cents,
    case
        when count(distinct l.square_order_id) > 0
            then round(sum(l.net_sales_cents) * 1.0 / count(distinct l.square_order_id))
        else 0
    end as avg_order_value_cents
from periods p
join lines l on l.sale_date between p.period_start and p.period_end
group by 1, 2, 3, 4, 5
order by p.period_order, day_of_week
