-- NOTE: valid_from/valid_to/is_current here are still placeholders matching
-- sql/warehouse_schema.sql's target shape -- every row reflects only the
-- current Silver snapshot, so is_current is always true.
--
-- dim_item now has real Type 2 history (see dim_item_history); locations
-- deliberately do not. There are two of them, one of which has never sold
-- anything, and neither has changed name or timezone in the extract's whole
-- span. Snapshotting them would add a stateful object to maintain in exchange
-- for a table with one version per row. The pattern to copy is next door if
-- that ever stops being true.
select
    row_number() over (order by location_id) as location_key,
    location_id as square_location_id,
    location_name,
    timezone,
    current_timestamp as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from {{ ref('stg_locations') }}
