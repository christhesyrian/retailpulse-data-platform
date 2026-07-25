-- Per-item weekly sales forecast for the next 4 weeks.
--
-- Method (deliberately simple and explainable, not a black box): fit a
-- linear trend (ordinary least squares via DuckDB regr_slope/regr_intercept)
-- to each item's weekly units over the last up-to-8 COMPLETE weeks, then
-- project it forward. Items with fewer than 2 weeks of history fall back to
-- their average weekly units. Forecasts are floored at 0 and rounded.
--
-- These are estimates. Accuracy improves with more history, and calendar
-- seasonality (holiday weeks, etc.) is NOT modeled yet — that needs a year+
-- of data and is a planned enhancement. `method` records which path produced
-- each number so the dashboard can be honest about it.
with anchors as (
    select date_trunc('week', current_date)::date as this_week_start
),

complete_weeks as (
    select w.variation_id, w.item_name, w.week_start, w.units_sold
    from {{ ref('kpi_item_weekly_sales') }} w
    cross join anchors a
    where w.week_start < a.this_week_start
      and w.week_start >= (a.this_week_start - interval 8 week)
),

indexed as (
    select
        variation_id,
        item_name,
        units_sold,
        row_number() over (partition by variation_id order by week_start) as wk_idx
    from complete_weeks
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
    from indexed
    group by variation_id
),

horizon as (
    select unnest(generate_series(1, 4)) as h
)

-- weeks_ahead = 1 is NEXT week (the current partial week is excluded — its
-- actuals-so-far live in kpi_item_sales.units_this_week instead). The fit's
-- most recent point is last complete week (index last_idx); the current
-- partial week would be last_idx + 1, so next week is last_idx + 2, etc.
select
    f.variation_id,
    f.item_name,
    (a.this_week_start + h.h * interval 7 day)::date as forecast_week_start,
    h.h as weeks_ahead,
    f.weeks_of_history,
    case
        when f.slope is not null
            then greatest(0, round(f.intercept + f.slope * (f.last_idx + 1 + h.h)))
        else greatest(0, round(coalesce(f.avg_units, 0)))
    end as forecast_units,
    case when f.slope is not null then 'linear_trend' else 'avg_fallback' end as method
from fit f
cross join horizon h
cross join anchors a
order by f.item_name, forecast_week_start
