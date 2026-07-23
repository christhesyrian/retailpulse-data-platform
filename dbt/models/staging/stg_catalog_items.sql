select
    variation_id,
    item_id,
    item_name,
    variation_name,
    category_id,
    category_name,
    price_cents,
    currency,
    sku,
    is_deleted,
    try_cast(updated_at as timestamp) as updated_at
from {{ source('silver', 'catalog_items') }}
