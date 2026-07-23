select
    order_id,
    line_item_uid,
    location_id,
    catalog_object_id,
    item_name,
    variation_name,
    try_cast(quantity as decimal(18, 4)) as quantity,
    gross_sales_cents,
    discount_cents,
    tax_cents,
    net_sales_cents,
    currency,
    order_state,
    try_cast(order_created_at as timestamp) as order_created_at,
    try_cast(order_updated_at as timestamp) as order_updated_at,
    try_cast(closed_at as timestamp) as closed_at
from {{ source('silver', 'order_lines') }}
