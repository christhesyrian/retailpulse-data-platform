-- Fails the build if any order and its payment(s) both exist but their
-- totals disagree. Orphaned orders/payments (one side missing) are
-- expected in the Sandbox and are intentionally NOT flagged here.
select *
from {{ ref('rpt_order_payment_reconciliation') }}
where reconciliation_status = 'mismatch'
