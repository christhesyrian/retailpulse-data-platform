-- Gross profit and margin by vendor. Lines with no vendor cost on file have
-- vendor_name NULL and are excluded from the per-vendor rollup.
select
    vendor_name,
    count(*) as order_lines,
    sum(net_sales_cents) as net_sales_cents,
    sum(cogs_cents) as cogs_cents,
    sum(gross_profit_cents) as gross_profit_cents,
    case
        when sum(net_sales_cents) > 0
            then round(100.0 * sum(gross_profit_cents) / sum(net_sales_cents), 2)
    end as gross_margin_pct
from {{ ref('fact_order_line_margin') }}
where has_cost
group by vendor_name
order by gross_profit_cents desc nulls last
