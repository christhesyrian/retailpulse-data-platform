-- Net sales by product category, with each category's share of the total.
with lines as (
    select
        f.square_order_id,
        f.quantity,
        f.net_sales_cents,
        coalesce(i.category_name, 'Uncategorized') as category_name
    from {{ ref('fact_order_line') }} f
    left join {{ ref('dim_item') }} i on f.item_key = i.item_key
),

total as (
    select sum(net_sales_cents) as total_net_sales_cents from lines
)

select
    l.category_name,
    count(distinct l.square_order_id) as orders,
    sum(l.quantity) as units,
    sum(l.net_sales_cents) as net_sales_cents,
    round(100.0 * sum(l.net_sales_cents) / nullif((select total_net_sales_cents from total), 0), 2)
        as pct_of_net_sales
from lines l
group by l.category_name
order by net_sales_cents desc
