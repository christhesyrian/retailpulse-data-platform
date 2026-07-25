-- Optional category rename map. Both sides are normalized the same way as
-- catalog categories so the join is robust to case/spacing in the CSV.
-- Rows where raw and canonical match (or canonical is blank) are dropped so
-- they don't count as real overrides. Deduplicated on raw_norm so a repeated
-- entry in the CSV can never fan out the item dimension.
with raw as (
    select
        {{ normalize_category('raw_category') }} as raw_norm,
        {{ normalize_category('canonical_category') }} as canonical_norm
    from {{ source('reference', 'category_overrides') }}
    where raw_category is not null
)

select
    raw_norm,
    max(canonical_norm) as canonical_category
from raw
where canonical_norm is not null
  and canonical_norm <> raw_norm
group by raw_norm
