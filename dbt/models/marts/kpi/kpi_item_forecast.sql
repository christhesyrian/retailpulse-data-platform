-- Per-item weekly sales forecast for the next 4 weeks.
--
-- Method (deliberately simple and explainable, not a black box): fit a
-- linear trend (ordinary least squares via DuckDB regr_slope/regr_intercept)
-- to each item's weekly units over the last up-to-8 COMPLETE weeks, then
-- project it forward. Items with fewer than 2 weeks of history fall back to
-- their average weekly units. Forecasts are floored at 0 and rounded.
--
-- Two things this model has to get right, because kpi_item_weekly_sales only
-- emits rows for weeks an item actually sold in:
--
--   1. Weeks with no sales must be filled in as explicit zeros. Fitting on
--      the sold-weeks alone measures "units in the weeks it sold", not "units
--      per week", which overstates both the level and the slope for anything
--      that sells intermittently — most of a 2,600-item catalog.
--   2. The regression's x-axis must be the real week offset, not the row
--      number, so a gap in the series reads as a gap instead of being
--      silently compressed.
--
-- An item's series starts the week it first sold inside the window, so a
-- newly-stocked item isn't padded with zeros from before it existed.
--
-- These are estimates. Accuracy improves with more history, and calendar
-- seasonality (holiday weeks, etc.) is NOT modeled yet — that needs a year+
-- of data and is a planned enhancement. `method` records which path produced
-- each number so the dashboard can be honest about it.
with anchors as (
    select
        date_trunc('week', current_date)::date as this_week_start,
        (date_trunc('week', current_date) - interval 8 week)::date as window_start
),

-- Every complete ISO week in the fit window, sales or not.
weeks as (
    select unnest(generate_series(
        (select window_start from anchors),
        (select this_week_start from anchors) - interval 7 day,
        interval 7 day
    ))::date as week_start
),

actuals as (
    select w.variation_id, w.item_name, w.week_start, w.units_sold
    from {{ ref('kpi_item_weekly_sales') }} w
    cross join anchors a
    where w.week_start < a.this_week_start
      and w.week_start >= a.window_start
),

item_span as (
    select
        variation_id,
        max(item_name) as item_name,
        min(week_start) as first_week
    from actuals
    group by variation_id
),

dense as (
    select
        s.variation_id,
        s.item_name,
        wk.week_start,
        coalesce(a.units_sold, 0) as units_sold,
        {{ date_diff_in('week', 's.first_week', 'wk.week_start') }} as wk_idx
    from item_span s
    join weeks wk on wk.week_start >= s.first_week
    left join actuals a
        on a.variation_id = s.variation_id
       and a.week_start = wk.week_start
),

fit as (
    select
        variation_id,
        max(item_name) as item_name,
        regr_slope(units_sold, wk_idx) as slope,
        regr_intercept(units_sold, wk_idx) as intercept,
        max(wk_idx) as last_idx,
        avg(units_sold) as avg_units,
        count(*) as weeks_of_history
    from dense
    group by variation_id
),

horizon as (
    select unnest(generate_series(1, 4)) as h
)

-- weeks_ahead = 1 is NEXT week (the current partial week is excluded — its
-- actuals-so-far live in kpi_item_sales.units_this_week instead). The fit's
-- most recent point is last complete week (index last_idx); the current
-- partial week would be last_idx + 1, so next week is last_idx + 2, etc.
--
-- A single-point series makes regr_slope return NaN rather than NULL, and
-- greatest(0, NULL) collapses to 0 — so an `is not null` guard alone would
-- silently forecast zero for every brand-new item. Check for NaN explicitly.
select
    f.variation_id,
    f.item_name,
    (a.this_week_start + h.h * interval 7 day)::date as forecast_week_start,
    h.h as weeks_ahead,
    f.weeks_of_history,
    case
        when f.weeks_of_history >= 2 and f.slope is not null and not isnan(f.slope)
            then greatest(0, round(f.intercept + f.slope * (f.last_idx + 1 + h.h)))
        else greatest(0, round(coalesce(f.avg_units, 0)))
    end as forecast_units,
    case
        when f.weeks_of_history >= 2 and f.slope is not null and not isnan(f.slope)
            then 'linear_trend'
        else 'avg_fallback'
    end as method
from fit f
cross join horizon h
cross join anchors a
order by f.item_name, forecast_week_start
