-- Every projection must be a real, non-negative number.
--
-- NaN is the specific failure this guards. regr_slope returns NaN (not NULL)
-- for a single-point series, so an `is not null` guard passes it through, and
-- because greatest(0, NULL) collapses to 0 the damage showed up as items
-- silently forecasting zero rather than as an error. A not_null column test
-- cannot catch either shape.
select
    variation_id,
    forecast_week_start,
    forecast_units
from {{ ref('kpi_item_forecast') }}
where forecast_units is null
   or isnan(forecast_units)
   or forecast_units < 0
