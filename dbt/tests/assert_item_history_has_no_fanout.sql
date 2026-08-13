-- A point-in-time join against the history must return exactly one row.
--
-- This is the assertion that makes the Type 2 dimension safe to use, and it
-- guards the specific mistake everyone makes with one: joining a fact to a
-- history table on the item key alone.
--
--     join dim_item_history h on f.item_key = h.item_key          -- WRONG
--
-- That is correct while every item has one version and silently starts
-- multiplying rows the first time anything is renamed or repriced. Revenue
-- doubles for the affected items, no test fails, and the total is simply
-- larger than it was yesterday.
--
-- The right join carries a validity predicate, which is what this checks:
--
--     join dim_item_history h
--       on f.item_key = h.item_key
--      and f.closed_at >= h.valid_from
--      and (f.closed_at < h.valid_to or h.valid_to is null)
--
-- Rows are counted rather than compared, because the property under test is
-- cardinality: one item, one instant, one version.
--
-- Sales that predate the first snapshot legitimately match nothing — history
-- starts when the snapshot was first taken, not when the shop opened — so a
-- zero-match row is expected and only a count above one is a fault.
with matches as (
    select
        f.order_line_key,
        count(*) as versions_in_effect
    from {{ ref('fact_order_line') }} f
    join {{ ref('dim_item_history') }} h
        on f.item_key = h.item_key
       and f.closed_at >= h.valid_from
       and (h.valid_to is null or f.closed_at < h.valid_to)
    where f.closed_at is not null
    group by f.order_line_key
)

select order_line_key, versions_in_effect
from matches
where versions_in_effect > 1
