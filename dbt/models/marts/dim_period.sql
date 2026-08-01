-- The time windows the dashboard offers, and the bounds every period-aware
-- KPI model joins against.
--
-- Defining the windows once here is what lets the dashboard switch periods
-- without doing any date maths of its own: it picks a period_label and every
-- model already has a row for it.
--
-- Two decisions worth knowing about:
--
-- 1. Windows are anchored on the most recent day with sales, not on
--    current_date. If the extract is a day or two stale, anchoring on today
--    would put empty days inside the current window and compare them against
--    a full prior window — a 7-day view would read ~14% down per missing day
--    purely as an artefact. Anchoring on the data keeps current and prior
--    windows the same effective length. kpi_data_coverage reports how stale
--    the anchor is.
--
-- 2. Each window carries the equivalent window immediately before it, plus
--    prior_window_complete: whether that earlier window is fully inside the
--    extracted history. Comparing against a window the extract only partly
--    covers produces nonsense (a 90-day view against 2 days of prior data
--    reads as +4,659%), so downstream models null their change figures out
--    when this flag is false rather than publishing a number that looks real.
with anchor as (
    select
        coalesce(min(sale_date), current_date) as first_sale_date,
        coalesce(max(sale_date), current_date) as as_of_date
    from {{ ref('fact_order_line') }}
    where sale_date is not null
),

defs as (
    select * from (values
        ('Last 7 days', 7, 1),
        ('Last 30 days', 30, 2),
        ('Last 90 days', 90, 3),
        ('Last 365 days', 365, 4),
        ('All time', cast(null as integer), 5)
    ) as t(period_label, period_days, period_order)
)

select
    d.period_label,
    d.period_days,
    d.period_order,
    a.as_of_date,
    case
        when d.period_days is null then a.first_sale_date
        else a.as_of_date - d.period_days + 1
    end as period_start,
    a.as_of_date as period_end,
    case
        when d.period_days is not null then a.as_of_date - 2 * d.period_days + 1
    end as prior_start,
    case when d.period_days is not null then a.as_of_date - d.period_days end as prior_end,
    coalesce(
        d.period_days is not null and (a.as_of_date - 2 * d.period_days + 1) >= a.first_sale_date,
        false
    ) as prior_window_complete
from defs d
cross join anchor a
order by d.period_order
