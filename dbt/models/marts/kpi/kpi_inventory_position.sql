-- Current stock position per item variation **per location**, with a rough
-- days-of-inventory estimate and a reorder signal. Sales velocity is units sold
-- over a trailing 30-day window at that same location;
-- days_of_inventory = on_hand / (units_sold_30d / 30).
--
-- This is a first-pass reorder heuristic, not the demand-forecast-driven
-- recommendation planned for M9 — thresholds are fixed, not learned.
--
-- The grain used to be one row per variation, with the location dropped and a
-- `unique` test on the catalog object id. That held only because every item in
-- the real store happens to be stocked at exactly one location (1,585 snapshot
-- rows, 1,585 variations), so a single-store assumption sat inside a two-store
-- warehouse without ever failing. Stocking one catalog at both locations is what
-- surfaced it.
--
-- Velocity is grouped by location as well, and that is the half of the regrain
-- that actually matters. Keeping it chain-wide while graining stock per store
-- divides one location's shelf by both locations' sales, which understates
-- days-of-inventory by roughly the number of stores and makes `reorder_soon`
-- fire on a shelf that is fine. A wrong number, not a missing one.
with velocity as (
    select
        catalog_object_id,
        location_key,
        sum(quantity) as units_sold_30d
    from {{ ref('fact_order_line') }}
    where sale_date >= {{ add_days('current_date', -30) }}
    group by catalog_object_id, location_key
),

-- The latest count per (variation, location), chosen explicitly rather than
-- assumed. fact_inventory_snapshot is grained per variation/location/snapshot
-- time and its own comment reserves the right to accumulate snapshots in a
-- later milestone; today Square returns one current count per pair, so this
-- picks the single row it already has. Written this way, the uniqueness test
-- below is true by construction instead of true by luck, and the model does not
-- silently start double-counting the first time history is retained.
ranked as (
    select
        square_catalog_object_id,
        item_key,
        location_key,
        square_location_id,
        quantity_on_hand,
        calculated_at,
        row_number() over (
            partition by square_catalog_object_id, location_key
            order by calculated_at desc
        ) as recency
    from {{ ref('fact_inventory_snapshot') }}
    -- Explicit, though every row carries it today. Square's batch-retrieve can
    -- return other states, and a future extract that widened the request would
    -- otherwise add rows here that are not on-hand stock at all.
    where state = 'IN_STOCK'
),

snapshot as (
    select * from ranked where recency = 1
)

select
    {{ surrogate_key(['s.square_catalog_object_id', 's.square_location_id']) }}
        as inventory_position_key,
    s.square_catalog_object_id,
    s.location_key,
    s.square_location_id,
    l.location_name,
    i.item_name,
    i.category_name,
    s.quantity_on_hand,
    coalesce(v.units_sold_30d, 0) as units_sold_30d,
    round(coalesce(v.units_sold_30d, 0) / 30.0, 3) as avg_daily_units,
    round(s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0), 1) as days_of_inventory,
    case
        when coalesce(v.units_sold_30d, 0) = 0 then 'no_recent_sales'
        when s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0) < 7 then 'reorder_soon'
        when s.quantity_on_hand / nullif(v.units_sold_30d / 30.0, 0) < 14 then 'watch'
        else 'ok'
    end as stock_status,
    s.calculated_at
from snapshot s
left join {{ ref('dim_item') }} i on s.item_key = i.item_key
left join {{ ref('dim_location') }} l on s.location_key = l.location_key
left join velocity v
    on s.square_catalog_object_id = v.catalog_object_id
   and s.location_key = v.location_key
order by days_of_inventory nulls last
