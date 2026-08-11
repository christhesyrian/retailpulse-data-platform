-- dim_date's spine must reach every day that has sales.
--
-- The spine is an offset counter with a compile-time ceiling (see dim_date),
-- chosen because no row-generating function is portable across DuckDB,
-- Snowflake and BigQuery. A ceiling is a truncation risk: exceed it and the
-- calendar simply stops, every daily time-series loses its tail, and nothing
-- errors -- the numbers that remain are all correct, there are just fewer of
-- them. That is precisely the failure this project treats as worse than a
-- crash, so it is asserted rather than trusted.
with sales as (
    select
        min(sale_date) as first_sale_date,
        max(sale_date) as last_sale_date
    from {{ ref('fact_order_line') }}
    where sale_date is not null
),

calendar as (
    select
        min(date_day) as first_day,
        max(date_day) as last_day,
        count(*) as days_in_calendar
    from {{ ref('dim_date') }}
)

select
    s.first_sale_date,
    s.last_sale_date,
    c.first_day,
    c.last_day,
    c.days_in_calendar
from sales s
cross join calendar c
where c.last_day < s.last_sale_date
   or c.first_day > s.first_sale_date
   -- A gap-free spine has exactly one row per day in the range.
   or c.days_in_calendar <> {{ date_diff_in('day', 'c.first_day', 'c.last_day') }} + 1
