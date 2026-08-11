-- Every period-aware KPI model must hold exactly one row per
-- (period, key). Adding the period dimension turned what used to be
-- single-column primary keys into composite ones, which dbt's built-in
-- `unique` test can't express without dbt_utils — so it's asserted here.
--
-- A duplicate here would double-count a category or an item inside a period,
-- which is exactly the class of bug that inflated the forecast before
-- kpi_item_weekly_sales was regrouped.
-- `row_count`, not `rows`: the latter is a reserved word on Snowflake and
-- fails to parse there while being a perfectly good alias on DuckDB.
select 'kpi_summary' as model, period_label, cast('' as {{ string_type() }}) as key_2, count(*) as row_count
from {{ ref('kpi_summary') }}
group by 1, 2, 3
having count(*) > 1

union all

select 'kpi_sales_by_category', period_label, category_name, count(*)
from {{ ref('kpi_sales_by_category') }}
group by 1, 2, 3
having count(*) > 1

union all

select 'kpi_sales_by_weekday', period_label, cast(day_of_week as {{ string_type() }}), count(*)
from {{ ref('kpi_sales_by_weekday') }}
group by 1, 2, 3
having count(*) > 1

union all

select 'kpi_sales_by_hour', period_label, cast(hour_of_day as {{ string_type() }}), count(*)
from {{ ref('kpi_sales_by_hour') }}
group by 1, 2, 3
having count(*) > 1

union all

select 'kpi_payment_methods', period_label, source_type, count(*)
from {{ ref('kpi_payment_methods') }}
group by 1, 2, 3
having count(*) > 1

union all

select 'kpi_item_sales', period_label, variation_id, count(*)
from {{ ref('kpi_item_sales') }}
group by 1, 2, 3
having count(*) > 1
