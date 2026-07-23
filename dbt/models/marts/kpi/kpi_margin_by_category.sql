-- Gross profit and margin by product category. Only lines with a known
-- vendor cost (has_cost) are aggregated, so margin is never fabricated from
-- missing costs; line_cost_coverage_pct reports how complete the costing is.
with lines as (
    select
        coalesce(category_name, 'Uncategorized') as category_name,
        net_sales_cents,
        cogs_cents,
        gross_profit_cents,
        has_cost
    from {{ ref('fact_order_line_margin') }}
)

select
    category_name,
    count(*) as order_lines,
    round(100.0 * sum(case when has_cost then 1 else 0 end) / count(*), 1)
        as line_cost_coverage_pct,
    sum(case when has_cost then net_sales_cents end) as net_sales_cents,
    sum(cogs_cents) as cogs_cents,
    sum(gross_profit_cents) as gross_profit_cents,
    case
        when sum(case when has_cost then net_sales_cents end) > 0
            then round(100.0 * sum(gross_profit_cents)
                 / sum(case when has_cost then net_sales_cents end), 2)
    end as gross_margin_pct
from lines
group by category_name
order by gross_profit_cents desc nulls last
