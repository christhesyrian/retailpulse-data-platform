-- Calendar dimension covering the span of order activity. Built from a
-- generated date spine so days with zero sales still appear (important
-- for honest daily time-series -- a gap day should read as 0, not vanish).
with bounds as (
    select
        coalesce(min(cast(closed_at as date)), current_date) as min_date,
        coalesce(max(cast(closed_at as date)), current_date) as max_date
    from {{ ref('fact_order_line') }}
),

spine as (
    select cast(unnest(generate_series(
        (select min_date from bounds),
        (select max_date from bounds),
        interval 1 day
    )) as date) as date_day
)

select
    date_day,
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,
    extract(year from date_day) as year,
    extract(month from date_day) as month,
    monthname(date_day) as month_name,
    extract(day from date_day) as day_of_month,
    isodow(date_day) as day_of_week,          -- 1 = Monday ... 7 = Sunday
    dayname(date_day) as day_name,
    cast(extract(week from date_day) as integer) as week_of_year,
    isodow(date_day) in (6, 7) as is_weekend
from spine
