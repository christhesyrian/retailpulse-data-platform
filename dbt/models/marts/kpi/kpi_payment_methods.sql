-- Payment method mix: how much is collected by each tender type, and the
-- Square processing fees attributable to each.
with total as (
    select sum(amount_cents) as total_collected_cents from {{ ref('fact_payment') }}
)

select
    source_type,
    count(*) as payments,
    sum(amount_cents) as amount_collected_cents,
    sum(processing_fee_cents) as processing_fee_cents,
    round(100.0 * sum(amount_cents) / nullif((select total_collected_cents from total), 0), 2)
        as pct_of_collected
from {{ ref('fact_payment') }}
group by source_type
order by amount_collected_cents desc
