-- Expected takings for each of the next 31 days.
--
-- The question this answers is "how much money am I going to make", which is
-- not the question kpi_item_forecast answers. That model projects units for
-- 4,000 individual items; useful for reordering, useless for cash planning,
-- and summing it would double down on every per-item error at once.
--
-- Method, deliberately explainable rather than clever:
--
--   forecast(day) = average takings on that weekday over the last 8 weeks
--                   x a trend factor
--
-- Weekday matters more than anything else here. A liquor store's Friday is a
-- different business from its Tuesday, so a flat daily average would be wrong
-- in a different direction every day of the week and only look right when
-- summed. The trend factor is the last 4 weeks over the 4 before them, which
-- carries a real upswing or slump without letting one freak week dominate.
--
-- The factor is clamped to [0.5, 2.0]. Unclamped, a quiet fortnight next to a
-- holiday fortnight produces a multiplier that turns a plausible weekday
-- average into a fantasy, and the clamp is what stops one bad ratio from
-- propagating into every future day.
--
-- Honesty is in kpi_revenue_forecast, which backtests this against a naive
-- baseline and publishes both error rates.

with daily as (
    select sale_date, net_sales_cents
    from {{ ref('kpi_daily_sales') }}
    where net_sales_cents is not null
),

anchor as (
    -- Anchored on the last day with sales, not current_date: an extract that
    -- is two days stale would otherwise contribute two zero-sales days to
    -- every weekday average that touched them.
    select max(sale_date) as as_of_date from daily
),

window_8w as (
    select d.sale_date, d.net_sales_cents, a.as_of_date
    from daily d
    cross join anchor a
    where d.sale_date > a.as_of_date - 56
      and d.sale_date <= a.as_of_date
),

weekday_profile as (
    select
        isodow(sale_date) as day_of_week,
        avg(net_sales_cents) as avg_net_sales_cents,
        count(*) as observations
    from window_8w
    group by 1
),

trend as (
    select
        sum(case when sale_date > as_of_date - 28 then net_sales_cents end) as recent_cents,
        sum(case
            when sale_date <= as_of_date - 28 then net_sales_cents
        end) as prior_cents
    from window_8w
),

factor as (
    select
        coalesce(recent_cents * 1.0 / nullif(prior_cents, 0), 1.0) as raw_factor,
        greatest(0.5, least(2.0,
            coalesce(recent_cents * 1.0 / nullif(prior_cents, 0), 1.0)
        )) as trend_factor
    from trend
),

-- Cast at the source: range() yields BIGINT and DuckDB has no DATE + BIGINT
-- operator, so every date expression below would fail to bind.
horizon as (select cast(days_ahead as integer) as days_ahead from range(1, 32) as t(days_ahead))

select
    cast(a.as_of_date + h.days_ahead as date) as forecast_date,
    h.days_ahead,
    dayname(a.as_of_date + h.days_ahead) as day_name,
    isodow(a.as_of_date + h.days_ahead) as day_of_week,
    cast(round(w.avg_net_sales_cents * f.trend_factor) as bigint)
        as forecast_net_sales_cents,
    cast(round(w.avg_net_sales_cents) as bigint) as weekday_avg_cents,
    round(f.trend_factor, 3) as trend_factor,
    w.observations as weeks_of_history,
    'weekday_average_x_trend' as method,
    a.as_of_date
from horizon h
cross join anchor a
cross join factor f
join weekday_profile w on w.day_of_week = isodow(a.as_of_date + h.days_ahead)
order by h.days_ahead
