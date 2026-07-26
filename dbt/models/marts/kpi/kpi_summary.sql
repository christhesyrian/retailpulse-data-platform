-- Headline KPIs, one row per period in dim_period.
--
-- net_sales excludes tax; collected_cents is what actually changed hands
-- (net + tax + tips), so collected should be at least net_sales + tax.
--
-- Every measure is also computed over the immediately preceding window of the
-- same length, so the dashboard can show "vs previous 30 days" without doing
-- arithmetic of its own. "All time" has no prior window, so its change
-- columns come out null.
with lines as (
    select
        cast(closed_at as date) as sale_date,
        order_line_key,
        square_order_id,
        quantity,
        gross_sales_cents,
        discount_cents,
        tax_cents,
        net_sales_cents
    from {{ ref('fact_order_line') }}
    where closed_at is not null
),

payments as (
    select
        cast(created_at as date) as pay_date,
        amount_cents,
        processing_fee_cents
    from {{ ref('fact_payment') }}
),

-- Margin carries no date of its own; it inherits the order line's sale date.
margin_lines as (
    select
        l.sale_date,
        m.has_cost,
        m.net_sales_cents,
        m.cogs_cents,
        m.gross_profit_cents
    from {{ ref('fact_order_line_margin') }} m
    join lines l on m.order_line_key = l.order_line_key
),

periods as (
    select * from {{ ref('dim_period') }}
),

current_lines as (
    select
        p.period_label,
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.gross_sales_cents) as gross_sales_cents,
        sum(l.discount_cents) as discount_cents,
        sum(l.tax_cents) as tax_cents,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.period_start and p.period_end
    group by p.period_label
),

prior_lines as (
    select
        p.period_label,
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.prior_start and p.prior_end
    group by p.period_label
),

current_payments as (
    select
        p.period_label,
        sum(y.amount_cents) as collected_cents,
        sum(y.processing_fee_cents) as processing_fee_cents
    from periods p
    join payments y on y.pay_date between p.period_start and p.period_end
    group by p.period_label
),

current_margin as (
    select
        p.period_label,
        sum(case when m.has_cost then m.net_sales_cents end) as costed_net_sales_cents,
        sum(m.cogs_cents) as cogs_cents,
        sum(m.gross_profit_cents) as gross_profit_cents,
        sum(m.net_sales_cents) as all_net_sales_cents
    from periods p
    join margin_lines m on m.sale_date between p.period_start and p.period_end
    group by p.period_label
)

select
    p.period_label,
    p.period_order,
    p.period_days,
    p.period_start,
    p.period_end,
    coalesce(c.orders, 0) as orders,
    coalesce(c.units, 0) as units,
    coalesce(c.gross_sales_cents, 0) as gross_sales_cents,
    coalesce(c.discount_cents, 0) as discount_cents,
    coalesce(c.tax_cents, 0) as tax_cents,
    coalesce(c.net_sales_cents, 0) as net_sales_cents,
    case
        when coalesce(c.orders, 0) > 0 then round(c.net_sales_cents * 1.0 / c.orders)
        else 0
    end as avg_order_value_cents,
    case
        when coalesce(c.orders, 0) > 0 then round(c.units * 1.0 / c.orders, 2)
        else 0
    end as units_per_order,
    coalesce(y.collected_cents, 0) as collected_cents,
    coalesce(y.processing_fee_cents, 0) as processing_fee_cents,

    -- Prior equivalent window and the change against it. Null (not zero) when
    -- there is no prior window, nothing sold in it, or the extract doesn't
    -- cover it fully — the dashboard shows "—" rather than a fabricated
    -- number. p.prior_window_complete is what makes the last case honest.
    p.prior_window_complete,
    case when p.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when p.prior_window_complete then pr.orders end as prior_orders,
    case when p.prior_window_complete then pr.units end as prior_units,
    case when p.prior_window_complete then round(
        100.0 * (coalesce(c.net_sales_cents, 0) - pr.net_sales_cents)
        / nullif(pr.net_sales_cents, 0), 1
    ) end as net_sales_change_pct,
    case when p.prior_window_complete then round(
        100.0 * (coalesce(c.orders, 0) - pr.orders) / nullif(pr.orders, 0), 1
    ) end as orders_change_pct,
    case when p.prior_window_complete then round(
        100.0 * (coalesce(c.units, 0) - pr.units) / nullif(pr.units, 0), 1
    ) end as units_change_pct,

    m.cogs_cents,
    m.gross_profit_cents,
    case
        when m.costed_net_sales_cents > 0
            then round(100.0 * m.gross_profit_cents / m.costed_net_sales_cents, 2)
    end as gross_margin_pct,
    case
        when m.all_net_sales_cents > 0
            then round(100.0 * m.costed_net_sales_cents / m.all_net_sales_cents, 1)
    end as cost_coverage_pct
from periods p
left join current_lines c on c.period_label = p.period_label
left join prior_lines pr on pr.period_label = p.period_label
left join current_payments y on y.period_label = p.period_label
left join current_margin m on m.period_label = p.period_label
order by p.period_order
