with payments as (
    select * from {{ ref('stg_payments') }}
),

locations as (
    select location_key, square_location_id, timezone from {{ ref('dim_location') }}
)

select
    payments.payment_id as square_payment_id,
    payments.order_id as square_order_id,
    locations.location_key,
    -- created_at stays as Square recorded it (naive UTC); pay_date is the
    -- store's own calendar day. See fact_order_line for why the conversion
    -- lives in the fact rather than in each consumer.
    payments.created_at,
    timezone(
        coalesce(locations.timezone, 'UTC'), timezone('UTC', payments.created_at)
    ) as created_at_local,
    cast(
        timezone(
            coalesce(locations.timezone, 'UTC'), timezone('UTC', payments.created_at)
        ) as date
    ) as pay_date,
    payments.updated_at,
    payments.status,
    payments.source_type,
    payments.amount_cents,
    payments.processing_fee_cents,
    payments.currency
from payments
left join locations on payments.location_id = locations.square_location_id
