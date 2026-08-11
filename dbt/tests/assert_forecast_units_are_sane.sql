-- Every projection must be a real, non-negative number.
--
-- NaN is the specific failure this guards. regr_slope returns NaN (not NULL)
-- for a single-point series on DuckDB, so an `is not null` guard passes it
-- through, and because greatest(0, NULL) collapses to 0 the damage showed up
-- as items silently forecasting zero rather than as an error. A not_null
-- column test cannot catch either shape.
--
-- Snowflake has no `isnan` at all, so the check goes through the portable
-- macro -- see is_nan() for why the two engines need different expressions.
select
    variation_id,
    forecast_week_start,
    forecast_units
from {{ ref('kpi_item_forecast') }}
where forecast_units is null
   or {{ is_nan('forecast_units') }}
   or forecast_units < 0
