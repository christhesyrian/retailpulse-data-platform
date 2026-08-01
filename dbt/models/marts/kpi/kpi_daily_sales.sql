-- Daily sales KPIs. Left-joined from dim_date so zero-sales days appear
-- as explicit 0 rows rather than gaps in the time series.
with lines as (
    select
        sale_date,
        square_order_id,
        quantity,
        gross_sales_cents,
        discount_cents,
        tax_cents,
        net_sales_cents
    from {{ ref('fact_order_line') }}
),

by_day as (
    select
        sale_date,
        count(distinct square_order_id) as orders,
        count(*) as line_items,
        sum(quantity) as units,
        sum(gross_sales_cents) as gross_sales_cents,
        sum(discount_cents) as discount_cents,
        sum(tax_cents) as tax_cents,
        sum(net_sales_cents) as net_sales_cents
    from lines
    group by sale_date
),

joined as (
    select
        d.date_day as sale_date,
        d.day_name,
        d.is_weekend,
        coalesce(b.orders, 0) as orders,
        coalesce(b.line_items, 0) as line_items,
        coalesce(b.units, 0) as units,
        coalesce(b.gross_sales_cents, 0) as gross_sales_cents,
        coalesce(b.discount_cents, 0) as discount_cents,
        coalesce(b.tax_cents, 0) as tax_cents,
        coalesce(b.net_sales_cents, 0) as net_sales_cents,
        case
            when coalesce(b.orders, 0) > 0 then round(b.net_sales_cents * 1.0 / b.orders)
            else 0
        end as avg_order_value_cents
    from {{ ref('dim_date') }} d
    left join by_day b on d.date_day = b.sale_date
)

-- Trailing 7-day average smooths the weekday sawtooth so the trend is
-- readable on the daily chart. It stays null until a full 7 days exist,
-- rather than reporting a short window as if it were a week.
select
    j.*,
    case
        when count(*) over trailing_week = 7
            then round(avg(j.net_sales_cents) over trailing_week)
    end as net_sales_7d_avg_cents
from joined j
window trailing_week as (order by j.sale_date rows between 6 preceding and current row)
order by j.sale_date
