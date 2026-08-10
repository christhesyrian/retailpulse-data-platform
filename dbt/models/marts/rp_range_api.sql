-- Registers the parameterized range macros, and documents what exists.
--
-- The macros themselves are DuckDB objects, not dbt models, because dbt has no
-- concept of a relation you pass arguments to. They are created in this
-- model's post-hook so that dbt still owns their lifecycle: they are rebuilt
-- on every `dbt build`, from SQL that lives in version control, after the facts
-- they read have been built.
--
-- This table is the manifest. It exists so `select * from rp_range_api` answers
-- "what can I call, and with what?" from inside the warehouse, and so the
-- macros have a node in the dbt DAG (and therefore in the Dagster asset graph)
-- rather than being invisible side effects.
{{ config(post_hook = "{{ create_range_macros() }}") }}

-- depends_on: {{ ref('fact_order_line') }}
-- depends_on: {{ ref('fact_payment') }}
-- depends_on: {{ ref('fact_order_line_margin') }}
-- depends_on: {{ ref('dim_item') }}
-- depends_on: {{ ref('dim_period') }}

select * from (values
    ('rp_window',         'start DATE, end DATE', 'Window bounds, prior window, and whether the prior window is fully covered'),
    ('rp_summary_range',  'start DATE, end DATE', 'Headline KPIs with prior-window comparison — mirrors kpi_summary'),
    ('rp_category_range', 'start DATE, end DATE', 'Net sales by category — mirrors kpi_sales_by_category'),
    ('rp_weekday_range',  'start DATE, end DATE', 'Net sales by weekday — mirrors kpi_sales_by_weekday'),
    ('rp_hour_range',     'start DATE, end DATE', 'Net sales by hour of day — mirrors kpi_sales_by_hour'),
    ('rp_popular_times_range', 'start DATE, end DATE', 'Busyness by hour for each weekday, scaled to that day''s peak'),
    ('rp_payments_range', 'start DATE, end DATE', 'Collected and fees by payment method — mirrors kpi_payment_methods'),
    ('rp_items_range',    'start DATE, end DATE', 'Per-item units and revenue — mirrors kpi_item_sales')
) as t(macro_name, signature, description)
