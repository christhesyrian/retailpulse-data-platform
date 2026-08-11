-- Calendar dimension covering the span of order activity. Built from a
-- generated date spine so days with zero sales still appear (important
-- for honest daily time-series -- a gap day should read as 0, not vanish).
with bounds as (
    select
        coalesce(min(sale_date), current_date) as min_date,
        coalesce(max(sale_date), current_date) as max_date
    from {{ ref('fact_order_line') }}
),

-- Row generation is the least portable thing in the project, so the spine is
-- an offset counter added to min_date rather than any vendor's series
-- function. The counter's ceiling is fixed at compile time; 10,000 days is
-- ~27 years of trading, and assert_date_spine_covers_sales fails the build
-- rather than silently truncating the calendar if that is ever not enough.
spine as (
    select cast({{ add_days('b.min_date', 'o.n') }} as date) as date_day
    from bounds b
    cross join ({{ integers(0, 9999) }}) o
    where {{ add_days('b.min_date', 'o.n') }} <= b.max_date
)

select
    date_day,
    -- Arithmetic rather than a format string: DuckDB spells this `strftime`,
    -- Snowflake `to_char` and BigQuery `format_date`, all with different
    -- argument orders, whereas YYYYMMDD as a number is the same everywhere.
    cast(
        extract(year from date_day) * 10000
        + extract(month from date_day) * 100
        + extract(day from date_day)
    as integer) as date_key,
    extract(year from date_day) as year,
    extract(month from date_day) as month,
    {{ month_name('date_day') }} as month_name,
    extract(day from date_day) as day_of_month,
    {{ day_of_week_iso('date_day') }} as day_of_week,   -- 1 = Monday ... 7 = Sunday
    {{ day_name('date_day') }} as day_name,
    cast({{ week_of_year('date_day') }} as integer) as week_of_year,
    {{ day_of_week_iso('date_day') }} in (6, 7) as is_weekend
from spine
