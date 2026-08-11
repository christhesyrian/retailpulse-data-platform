-- fact_order_line.sale_date must be the STORE's calendar day, not UTC's.
--
-- The bug this guards was invisible: every figure still reconciled, the totals
-- were right, and only the shape was wrong — evening trade attributed to the
-- next day, the next weekday, and an hour-of-day peak at 22:00–06:00. Nothing
-- failed, so nothing flagged it.
--
-- Three independent checks, since a conversion applied twice, or in the wrong
-- direction, would still produce a plausible-looking date:
--
--   1. sale_date must equal closed_at_local's date. Catches the case where
--      one is converted and the other isn't.
--   2. closed_at_local must differ from closed_at by the location's real
--      UTC offset — which for a US store is a whole number of hours between
--      1 and 23, and must not be zero. A zero offset means the conversion
--      silently no-opped (a missing timezone, or ICU not loaded).
--   3. sale_date is null EXACTLY when closed_at is null. Square returns a
--      few order lines it never closed, which are genuinely undated; this
--      pins that as the only reason a date can go missing, so a conversion
--      that started dropping rows would fail rather than look like more of
--      the same.
--
-- Locations that genuinely have no timezone on file fall back to UTC by
-- design, so they're excluded from checks 1 and 2 rather than failed.
with nullability as (
    select square_order_id, square_line_item_uid, closed_at, sale_date
    from {{ ref('fact_order_line') }}
    where (closed_at is null) <> (sale_date is null)
),

lines as (
    select
        f.closed_at,
        f.closed_at_local,
        f.sale_date,
        l.timezone
    from {{ ref('fact_order_line') }} f
    left join {{ ref('dim_location') }} l on f.location_key = l.location_key
    where f.closed_at is not null
      and l.timezone is not null
      and l.timezone <> 'UTC'
)

select
    'offset_or_date_mismatch' as failure,
    cast(closed_at as {{ string_type() }}) as closed_at,
    cast(sale_date as {{ string_type() }}) as sale_date
from lines
where sale_date <> cast(closed_at_local as date)
   or {{ date_diff_in('hour', 'closed_at_local', 'closed_at') }} not between 1 and 23

union all

select
    'nullability_mismatch' as failure,
    cast(closed_at as {{ string_type() }}),
    cast(sale_date as {{ string_type() }})
from nullability
