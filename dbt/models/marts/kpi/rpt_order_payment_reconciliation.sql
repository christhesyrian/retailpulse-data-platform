-- Reconciliation: does the money recorded on order lines line up with what
-- was collected in payments, order by order?
--
-- For each order, recorded sales = sum(net_sales + tax) across its lines.
-- On REAL data the amount collected usually differs from that, and legitimately:
--   * tips — the customer pays MORE than sales+tax (the tip isn't a line item)
--   * refunds / comps / unpaid balances — collected is LESS
--   * penny rounding
-- so this is a soft, informational reconciliation, not a hard invariant.
--
-- Status (variance_cents = recorded order total - collected):
--   matched               |variance| <= 1 cent (ties, absorbing rounding)
--   overpaid              collected exceeds recorded by > 1 cent — typically TIPS
--   short                 collected is less than recorded by > 1 cent — worth review
--   order_without_payment order exists, no matching payment (open/unpaid, or window edge)
--   payment_without_order payment exists, no matching order (e.g. an order outside the
--                         extract window, or from a location whose orders weren't pulled)
--
-- tests/assert_order_payment_reconciled.sql flags 'short' rows as a WARNING (not a
-- build failure), since shorts can be legitimate. Exact, tip-aware reconciliation
-- (capturing order-level tip/total_money) is a documented future enhancement.
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
        when abs(o.order_total_cents - p.paid_cents) <= 1 then 'matched'
        when p.paid_cents > o.order_total_cents then 'overpaid'
        else 'short'
    end as reconciliation_status
from order_totals o
full outer join payment_totals p on o.square_order_id = p.square_order_id
