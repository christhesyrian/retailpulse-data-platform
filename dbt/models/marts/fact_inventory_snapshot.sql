-- One row per item variation, location, and snapshot time. Square's
-- batch-retrieve returns the current calculated count, so in practice this
-- is the latest on-hand quantity per variation/location; the grain leaves
-- room for accumulating snapshots over time in a later milestone.
with inventory as (
    select * from {{ ref('stg_inventory_snapshots') }}
),

items as (
    select item_key, square_catalog_object_id from {{ ref('dim_item') }}
),

locations as (
    select location_key, square_location_id from {{ ref('dim_location') }}
)

select
    row_number() over (
        order by inventory.variation_id, inventory.location_id, inventory.calculated_at
    ) as inventory_snapshot_key,
    inventory.variation_id as square_catalog_object_id,
    items.item_key,
    locations.location_key,
    inventory.location_id as square_location_id,
    inventory.state,
    inventory.quantity as quantity_on_hand,
    inventory.calculated_at
from inventory
left join items on inventory.variation_id = items.square_catalog_object_id
left join locations on inventory.location_id = locations.square_location_id
