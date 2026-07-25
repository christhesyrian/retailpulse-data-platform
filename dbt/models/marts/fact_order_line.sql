with order_lines as (
    select * from {{ ref('stg_order_lines') }}
),

locations as (
    select location_key, square_location_id from {{ ref('dim_location') }}
),

items as (
    select item_key, square_catalog_object_id from {{ ref('dim_item') }}
)

select
    row_number() over (order by order_lines.order_id, order_lines.line_item_uid) as order_line_key,
    order_lines.order_id as square_order_id,
    order_lines.line_item_uid as square_line_item_uid,
    order_lines.catalog_object_id,
    -- name captured on the line at sale time (survives later catalog edits/deletes)
    order_lines.item_name,
    order_lines.variation_name,
    locations.location_key,
    items.item_key,
    order_lines.closed_at,
    order_lines.quantity,
    order_lines.gross_sales_cents,
    order_lines.discount_cents,
    order_lines.tax_cents,
    order_lines.net_sales_cents
from order_lines
left join locations on order_lines.location_id = locations.square_location_id
-- left join: some seeded/legacy line items reference catalog objects that
-- have since been deleted or recreated, so item_key can legitimately be null.
left join items on order_lines.catalog_object_id = items.square_catalog_object_id
