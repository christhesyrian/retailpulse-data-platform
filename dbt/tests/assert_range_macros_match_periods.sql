-- The custom-range macros must agree with the precomputed KPI models.
--
-- This is the test that makes arbitrary date ranges trustworthy. The dashboard
-- now has two ways to answer "what were net sales": read a precomputed
-- `kpi_*` row for one of the five canonical windows, or call a range macro
-- with arbitrary bounds. Those are different code paths over the same facts,
-- and nothing else forces them to stay in step — a change to one could quietly
-- leave the other behind, and the dashboard would show a different number
-- depending on whether you picked "Last 30 days" or typed the same 30 dates.
--
-- So: for each canonical window, call every macro with that window's own
-- bounds and require the results to be identical. `is distinct from` is used
-- throughout because these columns are deliberately nullable — a comparison
-- suppressed by prior_window_complete must stay suppressed on both sides, and
-- `=` would let null vs null pass silently as unknown.
--
-- One documented divergence: kpi_item_sales publishes a null avg_weekly_units
-- for "All time" (its length depends on how much history was extracted, not on
-- a period definition), whereas a custom range always has a definite length.
-- The item comparison therefore runs only over the bounded windows.

-- Two dependencies dbt cannot infer, declared so the ordering is real rather
-- than accidental: the relations this test calls are published by
-- rp_range_api's post-hook, and calling one is not a `ref`; and dim_period is
-- read at compile time inside canonical_windows(), behind an `execute` guard
-- that hides the ref from the parser.
-- depends_on: {{ ref('rp_range_api') }}
-- depends_on: {{ ref('dim_period') }}

{#- Resolved outside the conditional below so the parser can still see them. -#}
{%- set kpi_summary = ref('kpi_summary') %}
{%- set kpi_sales_by_category = ref('kpi_sales_by_category') %}
{%- set kpi_sales_by_weekday = ref('kpi_sales_by_weekday') %}
{%- set kpi_sales_by_hour = ref('kpi_sales_by_hour') %}
{%- set kpi_payment_methods = ref('kpi_payment_methods') %}
{%- set kpi_item_sales = ref('kpi_item_sales') %}

{%- set windows = canonical_windows() %}

{%- if windows | length == 0 %}

-- Parse time: dim_period has not been read, so there is nothing to compare.
select
    cast(null as varchar) as macro_name,
    cast(null as varchar) as period_label,
    cast(null as varchar) as detail
where 1 = 0

{%- else %}

-- Each relation is evaluated once per canonical window and unioned, rather
-- than joined laterally to dim_period. See canonical_windows() for why the
-- bounds are constants: neither warehouse will outer-join against a correlated
-- table function, and Snowflake will not evaluate one per row at all.
with m_summary as (
    {{ range_over_periods('rp_summary_range', windows) }}
),
m_category as (
    {{ range_over_periods('rp_category_range', windows) }}
),
m_weekday as (
    {{ range_over_periods('rp_weekday_range', windows) }}
),
m_hour as (
    {{ range_over_periods('rp_hour_range', windows) }}
),
m_payments as (
    {{ range_over_periods('rp_payments_range', windows) }}
),
m_items as (
    {{ range_over_periods('rp_items_range', windows, with_period_days=true) }}
),

summary_mismatch as (
    select
        'rp_summary_range' as macro_name,
        r.period_label,
        'headline KPIs' as detail
    from m_summary r
    join {{ kpi_summary }} k on k.period_label = r.period_label
    where r.orders                  is distinct from k.orders
       or r.units                   is distinct from k.units
       or r.gross_sales_cents       is distinct from k.gross_sales_cents
       or r.discount_cents          is distinct from k.discount_cents
       or r.tax_cents               is distinct from k.tax_cents
       or r.net_sales_cents         is distinct from k.net_sales_cents
       or r.avg_order_value_cents   is distinct from k.avg_order_value_cents
       or r.units_per_order         is distinct from k.units_per_order
       or r.collected_cents         is distinct from k.collected_cents
       or r.processing_fee_cents    is distinct from k.processing_fee_cents
       or r.prior_window_complete   is distinct from k.prior_window_complete
       or r.prior_net_sales_cents   is distinct from k.prior_net_sales_cents
       or r.prior_orders            is distinct from k.prior_orders
       or r.prior_units             is distinct from k.prior_units
       or r.net_sales_change_pct    is distinct from k.net_sales_change_pct
       or r.orders_change_pct       is distinct from k.orders_change_pct
       or r.units_change_pct        is distinct from k.units_change_pct
),

category_mismatch as (
    select
        'rp_category_range' as macro_name,
        coalesce(r.period_label, k.period_label) as period_label,
        coalesce(r.category_name, k.category_name) as detail
    from m_category r
    full outer join {{ kpi_sales_by_category }} k
        on k.period_label = r.period_label
       and k.category_name = r.category_name
    where r.category_name           is distinct from k.category_name
       or r.orders                  is distinct from k.orders
       or r.units                   is distinct from k.units
       or r.net_sales_cents         is distinct from k.net_sales_cents
       or r.pct_of_net_sales        is distinct from k.pct_of_net_sales
       or r.prior_net_sales_cents   is distinct from k.prior_net_sales_cents
       or r.net_sales_change_pct    is distinct from k.net_sales_change_pct
),

weekday_mismatch as (
    select
        'rp_weekday_range' as macro_name,
        coalesce(r.period_label, k.period_label) as period_label,
        cast(coalesce(r.day_of_week, k.day_of_week) as varchar) as detail
    from m_weekday r
    full outer join {{ kpi_sales_by_weekday }} k
        on k.period_label = r.period_label
       and k.day_of_week = r.day_of_week
    where r.day_name                is distinct from k.day_name
       or r.is_weekend              is distinct from k.is_weekend
       or r.orders                  is distinct from k.orders
       or r.net_sales_cents         is distinct from k.net_sales_cents
       or r.avg_order_value_cents   is distinct from k.avg_order_value_cents
),

hour_mismatch as (
    select
        'rp_hour_range' as macro_name,
        coalesce(r.period_label, k.period_label) as period_label,
        cast(coalesce(r.hour_of_day, k.hour_of_day) as varchar) as detail
    from m_hour r
    full outer join {{ kpi_sales_by_hour }} k
        on k.period_label = r.period_label
       and k.hour_of_day = r.hour_of_day
    where r.orders                  is distinct from k.orders
       or r.net_sales_cents         is distinct from k.net_sales_cents
),

payments_mismatch as (
    select
        'rp_payments_range' as macro_name,
        coalesce(r.period_label, k.period_label) as period_label,
        coalesce(r.source_type, k.source_type) as detail
    from m_payments r
    full outer join {{ kpi_payment_methods }} k
        on k.period_label = r.period_label
       and k.source_type = r.source_type
    where r.payments                is distinct from k.payments
       or r.amount_collected_cents  is distinct from k.amount_collected_cents
       or r.processing_fee_cents    is distinct from k.processing_fee_cents
       or r.pct_of_collected        is distinct from k.pct_of_collected
),

items_mismatch as (
    select
        'rp_items_range' as macro_name,
        coalesce(r.period_label, k.period_label) as period_label,
        coalesce(r.variation_id, k.variation_id) as detail
    from m_items r
    full outer join {{ kpi_item_sales }} k
        on k.period_label = r.period_label
       and k.variation_id = r.variation_id
    -- Bounded windows only; see the avg_weekly_units note above.
    where r.period_days is not null
      and (r.item_name                is distinct from k.item_name
        or r.variation_name           is distinct from k.variation_name
        or r.category_name            is distinct from k.category_name
        or r.units                    is distinct from k.units
        or r.orders                   is distinct from k.orders
        or r.net_sales_cents          is distinct from k.net_sales_cents
        or r.avg_weekly_units         is distinct from k.avg_weekly_units
        or r.prior_units              is distinct from k.prior_units
        or r.prior_net_sales_cents    is distinct from k.prior_net_sales_cents
        or r.units_change_pct         is distinct from k.units_change_pct
        or r.net_sales_change_pct     is distinct from k.net_sales_change_pct
        or r.units_all_time           is distinct from k.units_all_time
        or r.first_sold_at            is distinct from k.first_sold_at
        or r.last_sold_at             is distinct from k.last_sold_at)
)

select * from summary_mismatch
union all select * from category_mismatch
union all select * from weekday_mismatch
union all select * from hour_mismatch
union all select * from payments_mismatch
union all select * from items_mismatch

{%- endif %}
