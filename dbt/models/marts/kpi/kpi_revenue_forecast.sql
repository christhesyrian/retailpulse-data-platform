-- Expected takings over the horizons a shopkeeper actually plans against:
-- tomorrow, the next week, the next month. One row each.
--
-- Every figure is the sum of kpi_revenue_forecast_daily over that horizon, so
-- the dashboard adds nothing up itself and the daily chart and the headline
-- number can never disagree.
--
-- The columns that matter most here are the two error rates. A forecast that
-- does not say how wrong it usually is invites the reader to trust it
-- completely, which is worse than no forecast. So this backtests itself:
--
--   * Hold out the most recent 28 days.
--   * Fit the same weekday-average-times-trend method on everything before them.
--   * Predict those 28 days and measure the weighted absolute percentage error
--     (see the note on `errors` for why weighted rather than plain MAPE).
--   * Do the same for a naive baseline — one flat daily average — and publish
--     both.
--
-- If `wape_pct` is not comfortably below `baseline_wape_pct`, the weekday
-- model is earning nothing over an average, and the honest move is to say so
-- rather than keep the more complicated method because it took longer to
-- write. The dashboard prints both.

with daily as (
    select sale_date, net_sales_cents
    from {{ ref('kpi_daily_sales') }}
    where net_sales_cents is not null
),

anchor as (
    select max(sale_date) as as_of_date, min(sale_date) as first_date from daily
),

-- --- backtest -----------------------------------------------------------
-- Everything below `split_date` trains; the 28 days above it are scored.
split as (
    select as_of_date - 28 as split_date, as_of_date, first_date from anchor
),

train as (
    select d.sale_date, d.net_sales_cents, s.split_date
    from daily d
    cross join split s
    where d.sale_date <= s.split_date
      and d.sale_date > s.split_date - 56
),

train_weekday as (
    select isodow(sale_date) as day_of_week, avg(net_sales_cents) as avg_cents
    from train
    group by 1
),

train_trend as (
    select greatest(0.5, least(2.0, coalesce(
        sum(case when sale_date > split_date - 28 then net_sales_cents end) * 1.0
        / nullif(sum(case when sale_date <= split_date - 28 then net_sales_cents end), 0),
        1.0))) as trend_factor
    from train
),

train_baseline as (
    select avg(net_sales_cents) as flat_avg_cents from train
),

holdout as (
    select d.sale_date, d.net_sales_cents
    from daily d
    cross join split s
    where d.sale_date > s.split_date
),

scored as (
    select
        h.net_sales_cents as actual_cents,
        w.avg_cents * t.trend_factor as predicted_cents,
        b.flat_avg_cents as baseline_cents
    from holdout h
    join train_weekday w on w.day_of_week = isodow(h.sale_date)
    cross join train_trend t
    cross join train_baseline b
),

-- Weighted absolute percentage error, not MAPE.
--
-- MAPE divides by each day's actual, so a single unusually quiet day dominates
-- the whole score: one partially-extracted day with $24 of sales against a
-- $3,400 forecast is a 14,000% error on its own, and it dragged the reported
-- figure to 464% while every ordinary day was within a few percent. That
-- number says nothing about the model.
--
-- WAPE divides total error by total revenue, so each day counts in proportion
-- to the money involved, which is also how the error is actually felt. MAE in
-- cents is published alongside it because "typically out by $X a day" is the
-- form a shopkeeper can act on.
errors as (
    select
        round(100.0 * sum(abs(actual_cents - predicted_cents)) / nullif(sum(actual_cents), 0), 1)
            as wape_pct,
        round(100.0 * sum(abs(actual_cents - baseline_cents)) / nullif(sum(actual_cents), 0), 1)
            as baseline_wape_pct,
        cast(round(avg(abs(actual_cents - predicted_cents))) as bigint) as mae_cents,
        count(*) as backtest_days
    from scored
),

-- --- horizons -----------------------------------------------------------
horizons as (
    select * from (values
        ('Tomorrow', 1, 1),
        ('Next 7 days', 7, 2),
        ('Next 30 days', 30, 3)
    ) as t(horizon_label, horizon_days, horizon_order)
),

forecast as (select * from {{ ref('kpi_revenue_forecast_daily') }})

select
    h.horizon_label,
    h.horizon_days,
    h.horizon_order,
    min(f.forecast_date) as period_start,
    max(f.forecast_date) as period_end,
    sum(f.forecast_net_sales_cents) as forecast_net_sales_cents,
    -- Per-day average across the horizon, so "Next 30 days" is comparable
    -- with "Tomorrow" without the reader dividing anything.
    cast(round(avg(f.forecast_net_sales_cents)) as bigint) as forecast_daily_avg_cents,
    max(f.trend_factor) as trend_factor,
    max(f.method) as method,
    e.wape_pct,
    e.baseline_wape_pct,
    e.mae_cents,
    e.backtest_days,
    -- The comparison stated once, here, rather than re-derived by every reader.
    round(e.baseline_wape_pct - e.wape_pct, 1) as points_better_than_baseline,
    max(f.as_of_date) as as_of_date
from horizons h
join forecast f on f.days_ahead <= h.horizon_days
cross join errors e
group by h.horizon_label, h.horizon_days, h.horizon_order,
         e.wape_pct, e.baseline_wape_pct, e.mae_cents, e.backtest_days
order by h.horizon_order
