-- NOTE: valid_from/valid_to/is_current are placeholder SCD2 columns matching
-- sql/warehouse_schema.sql's target shape. Real history tracking (dbt
-- snapshots) is not implemented yet -- every row here reflects only the
-- current Silver snapshot, so is_current is always true.
select
    row_number() over (order by location_id) as location_key,
    location_id as square_location_id,
    location_name,
    timezone,
    current_timestamp as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from {{ ref('stg_locations') }}
