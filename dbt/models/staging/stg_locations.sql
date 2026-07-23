select
    location_id,
    name as location_name,
    status,
    timezone,
    currency,
    country,
    business_name,
    merchant_id,
    try_cast(created_at as timestamp) as created_at
from {{ source('silver', 'locations') }}
