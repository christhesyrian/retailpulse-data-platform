select
    catalog_object_id as variation_id,
    catalog_object_type,
    location_id,
    state,
    quantity,
    {{ try_cast_as('calculated_at', 'timestamp') }} as calculated_at
from {{ source('silver', 'inventory_snapshots') }}
