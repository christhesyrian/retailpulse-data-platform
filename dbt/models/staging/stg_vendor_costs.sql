-- One cost row per variation. Deduplicated defensively in case a
-- hand-maintained CSV lists a variation twice (keeps the highest cost).
select
    variation_id,
    max(item_name) as item_name,
    max(category_name) as category_name,
    max(vendor_name) as vendor_name,
    max({{ try_cast_as('unit_cost_cents', 'bigint') }}) as unit_cost_cents
from {{ source('reference', 'vendor_costs') }}
where variation_id is not null
group by variation_id
