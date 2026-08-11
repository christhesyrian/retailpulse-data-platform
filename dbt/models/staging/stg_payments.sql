select
    payment_id,
    order_id,
    location_id,
    amount_cents,
    currency,
    status,
    source_type,
    card_brand,
    card_last_4,
    processing_fee_cents,
    {{ try_cast_as('created_at', 'timestamp') }} as created_at,
    {{ try_cast_as('updated_at', 'timestamp') }} as updated_at
from {{ source('silver', 'payments') }}
