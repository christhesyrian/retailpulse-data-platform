select
    catalog_object_id as variation_id,
    catalog_object_type,
    location_id,
    state,
    quantity,
    try_cast(calculated_at as timestamp) as calculated_at
from {{ source('silver', 'inventory_snapshots') }}
