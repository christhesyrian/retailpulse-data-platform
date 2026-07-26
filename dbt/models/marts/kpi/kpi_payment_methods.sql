-- Payment method mix, one row per (period, tender type): how much was
-- collected by each, and the Square processing fees attributable to it.
with payments as (
    select
        cast(created_at as date) as pay_date,
        source_type,
        amount_cents,
        processing_fee_cents
    from {{ ref('fact_payment') }}
),

periods as (
    select * from {{ ref('dim_period') }}
),

current_window as (
    select
        p.period_label,
        p.period_order,
        y.source_type,
        count(*) as payments,
        sum(y.amount_cents) as amount_collected_cents,
        sum(y.processing_fee_cents) as processing_fee_cents
    from periods p
    join payments y on y.pay_date between p.period_start and p.period_end
    group by p.period_label, p.period_order, y.source_type
),

period_totals as (
    select period_label, sum(amount_collected_cents) as total_collected_cents
    from current_window
    group by period_label
)

select
    c.period_label,
    c.period_order,
    c.source_type,
    c.payments,
    c.amount_collected_cents,
    c.processing_fee_cents,
    round(
        100.0 * c.amount_collected_cents / nullif(t.total_collected_cents, 0), 2
    ) as pct_of_collected
from current_window c
join period_totals t on t.period_label = c.period_label
order by c.period_order, c.amount_collected_cents desc
