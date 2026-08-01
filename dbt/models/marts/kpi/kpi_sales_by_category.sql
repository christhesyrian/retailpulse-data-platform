-- Net sales by product category, one row per (period, category), with each
-- category's share of that period's total and its change vs. the previous
-- equivalent window.
with lines as (
    select
        f.sale_date,
        f.square_order_id,
        f.quantity,
        f.net_sales_cents,
        coalesce(i.category_name, 'Uncategorized') as category_name
    from {{ ref('fact_order_line') }} f
    left join {{ ref('dim_item') }} i on f.item_key = i.item_key
    where f.sale_date is not null
),

periods as (
    select * from {{ ref('dim_period') }}
),

current_window as (
    select
        p.period_label,
        p.period_order,
        p.prior_window_complete,
        l.category_name,
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.period_start and p.period_end
    group by p.period_label, p.period_order, p.prior_window_complete, l.category_name
),

prior_window as (
    select
        p.period_label,
        l.category_name,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.prior_start and p.prior_end
    group by p.period_label, l.category_name
),

period_totals as (
    select period_label, sum(net_sales_cents) as total_net_sales_cents
    from current_window
    group by period_label
)

select
    c.period_label,
    c.period_order,
    c.category_name,
    c.orders,
    c.units,
    c.net_sales_cents,
    round(100.0 * c.net_sales_cents / nullif(t.total_net_sales_cents, 0), 2) as pct_of_net_sales,
    -- Suppressed unless the extract fully covers the prior window; see
    -- dim_period.prior_window_complete.
    case when c.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when c.prior_window_complete then round(
        100.0 * (c.net_sales_cents - pr.net_sales_cents) / nullif(pr.net_sales_cents, 0), 1
    ) end as net_sales_change_pct
from current_window c
join period_totals t on t.period_label = c.period_label
left join prior_window pr
    on pr.period_label = c.period_label
   and pr.category_name = c.category_name
order by c.period_order, c.net_sales_cents desc
