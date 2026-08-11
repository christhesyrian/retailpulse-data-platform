select
    order_id,
    line_item_uid,
    location_id,
    catalog_object_id,
    item_name,
    variation_name,
    {{ try_cast_as('quantity', decimal_type(18, 4)) }} as quantity,
    gross_sales_cents,
    discount_cents,
    tax_cents,
    net_sales_cents,
    currency,
    order_state,
    {{ try_cast_as('order_created_at', 'timestamp') }} as order_created_at,
    {{ try_cast_as('order_updated_at', 'timestamp') }} as order_updated_at,
    {{ try_cast_as('closed_at', 'timestamp') }} as closed_at
from {{ source('silver', 'order_lines') }}
