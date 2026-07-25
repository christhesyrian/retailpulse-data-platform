-- Surfaces orders where LESS was collected than recorded sales+tax ('short')
-- by more than a cent. This is a WARNING, not a build failure: shorts can be
-- legitimate (refunds, comps, unpaid balances) and real stores always have a
-- few. Overpaid orders (tips) are expected and intentionally not flagged.
{{ config(severity='warn') }}
select *
from {{ ref('rpt_order_payment_reconciliation') }}
where reconciliation_status = 'short'
