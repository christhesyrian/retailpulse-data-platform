-- The Type 2 history must be a clean timeline per item.
--
-- A slowly-changing dimension is only useful if, for any item and any instant,
-- exactly one version was in effect. Four ways that breaks, all of which
-- produce a table that looks fine until someone does a point-in-time join and
-- gets duplicated or missing rows:
--
--   * two versions open at once      -> the join returns both
--   * overlapping validity windows   -> the join returns both
--   * a gap between versions         -> the join returns nothing
--   * valid_to before valid_from     -> the version is never in effect
--
-- dbt's snapshot machinery is what maintains these, and it is reliable. The
-- test is here because the *model* on top reshapes it — deriving is_current
-- and version_number — and because a botched `--full-refresh` of the snapshot
-- is otherwise silent.
with history as (
    select
        item_key,
        item_version_key,
        valid_from,
        valid_to,
        is_current,
        version_number,
        lead(valid_from) over (partition by item_key order by valid_from) as next_valid_from
    from {{ ref('dim_item_history') }}
),

open_versions as (
    select item_key, count(*) as open_count
    from history
    where valid_to is null
    group by item_key
    having count(*) <> 1
),

broken_rows as (
    select
        item_key,
        item_version_key,
        case
            when valid_to is not null and valid_to < valid_from
                then 'valid_to precedes valid_from'
            -- A closed version must hand over exactly where the next one
            -- starts: no overlap, no gap.
            when next_valid_from is not null and valid_to is distinct from next_valid_from
                then 'does not meet the next version'
            when valid_to is null and next_valid_from is not null
                then 'open version is not the latest'
            when is_current <> (valid_to is null)
                then 'is_current disagrees with valid_to'
        end as problem
    from history
)

select item_key, item_version_key, problem
from broken_rows
where problem is not null

union all

select item_key, cast(null as {{ string_type() }}),
       'has ' || cast(open_count as {{ string_type() }}) || ' open versions, expected 1'
from open_versions
