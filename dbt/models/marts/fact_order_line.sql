with order_lines as (
    select * from {{ ref('stg_order_lines') }}
),

locations as (
    select location_key, square_location_id, timezone from {{ ref('dim_location') }}
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
    -- closed_at is kept exactly as Square recorded it: a naive timestamp
    -- holding UTC. It stays for lineage and for anyone reconciling against
    -- Square's own API responses.
    order_lines.closed_at,
    -- ...and is converted to the store's wall clock ONCE, here, using the
    -- timezone Square reports for the location. Every downstream model reads
    -- sale_date / closed_at_local instead of re-deriving from UTC.
    --
    -- This matters more than it looks for a liquor store: a 9pm sale is 04:00
    -- the NEXT day in UTC, so deriving dates from UTC pushed a large slice of
    -- every evening's trade onto the following day — and onto the following
    -- weekday. It also put the "when you sell" peak at 22:00–06:00, which is
    -- nobody's trading pattern.
    --
    -- The inner timezone() reads the naive timestamp as UTC and yields an
    -- instant; the outer one renders that instant as wall-clock time in the
    -- store's zone. It is DST-correct, so the offset is -7 in July and -8 in
    -- January rather than a fixed shift. Locations with no timezone on file
    -- fall back to UTC, which is the old behaviour rather than a wrong one.
    {{ to_local_time("coalesce(locations.timezone, 'UTC')", "order_lines.closed_at") }} as closed_at_local,
    cast(
        {{ to_local_time("coalesce(locations.timezone, 'UTC')", "order_lines.closed_at") }} as date
    ) as sale_date,
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
