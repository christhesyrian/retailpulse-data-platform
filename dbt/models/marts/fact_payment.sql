with payments as (
    select * from {{ ref('stg_payments') }}
),

locations as (
    select location_key, square_location_id from {{ ref('dim_location') }}
)

select
    payments.payment_id as square_payment_id,
    payments.order_id as square_order_id,
    locations.location_key,
    payments.created_at,
    payments.updated_at,
    payments.status,
    payments.source_type,
    payments.amount_cents,
    payments.processing_fee_cents,
    payments.currency
from payments
left join locations on payments.location_id = locations.square_location_id
