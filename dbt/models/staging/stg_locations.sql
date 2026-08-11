select
    location_id,
    name as location_name,
    status,
    timezone,
    currency,
    country,
    business_name,
    merchant_id,
    {{ try_cast_as('created_at', 'timestamp') }} as created_at
from {{ source('silver', 'locations') }}
