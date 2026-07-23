-- Internal reconciliation: does the money we recorded on order lines match
-- the money we recorded as collected in payments, order by order?
--
-- For each order, the amount collected should equal net sales + tax
-- (net_sales_cents excludes tax; the tender collects the tax-inclusive
-- total). A 'mismatch' means both an order and a payment exist for the id
-- but the totals disagree -- that would be a genuine pipeline defect and
-- is asserted to be zero by tests/assert_order_payment_reconciled.sql.
--
-- 'order_without_payment' / 'payment_without_order' are expected in the
-- Square Sandbox (e.g. orphaned records from re-seeding) and are surfaced
-- for visibility rather than failed on.
--
-- NOTE: this is *internal* reconciliation (the pipeline agrees with
-- itself). Reconciling against Square's own Reporting API / Dashboard
-- totals is a separate, production-only check -- see docs/architecture.md.
with order_totals as (
    select
        square_order_id,
        sum(net_sales_cents + tax_cents) as order_total_cents
    from {{ ref('fact_order_line') }}
    group by square_order_id
),

payment_totals as (
    select
        square_order_id,
        sum(amount_cents) as paid_cents
    from {{ ref('fact_payment') }}
    where square_order_id is not null
    group by square_order_id
)

select
    coalesce(o.square_order_id, p.square_order_id) as square_order_id,
    o.order_total_cents,
    p.paid_cents,
    coalesce(o.order_total_cents, 0) - coalesce(p.paid_cents, 0) as variance_cents,
    case
        when o.square_order_id is null then 'payment_without_order'
        when p.square_order_id is null then 'order_without_payment'
        when o.order_total_cents = p.paid_cents then 'matched'
        else 'mismatch'
    end as reconciliation_status
from order_totals o
full outer join payment_totals p on o.square_order_id = p.square_order_id
