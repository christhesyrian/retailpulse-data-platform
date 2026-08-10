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

with periods as (
    select * from {{ ref('dim_period') }}
),

-- DuckDB cannot outer-join against a correlated lateral, so each macro is
-- materialized once per canonical window first and compared afterwards.
m_summary as (
    select p.period_label, r.*
    from periods p, lateral rp_summary_range(p.period_start, p.period_end) r
),
m_category as (
    select p.period_label, r.*
    from periods p, lateral rp_category_range(p.period_start, p.period_end) r
),
m_weekday as (
    select p.period_label, r.*
    from periods p, lateral rp_weekday_range(p.period_start, p.period_end) r
),
m_hour as (
    select p.period_label, r.*
    from periods p, lateral rp_hour_range(p.period_start, p.period_end) r
),
m_payments as (
    select p.period_label, r.*
    from periods p, lateral rp_payments_range(p.period_start, p.period_end) r
),
m_items as (
    select p.period_label, p.period_days, r.*
    from periods p, lateral rp_items_range(p.period_start, p.period_end) r
),

summary_mismatch as (
    select
        'rp_summary_range' as macro_name,
        r.period_label,
        'headline KPIs' as detail
    from m_summary r
    join {{ ref('kpi_summary') }} k on k.period_label = r.period_label
    join periods p on p.period_label = r.period_label
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
    full outer join {{ ref('kpi_sales_by_category') }} k
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
    full outer join {{ ref('kpi_sales_by_weekday') }} k
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
    full outer join {{ ref('kpi_sales_by_hour') }} k
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
    full outer join {{ ref('kpi_payment_methods') }} k
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
    full outer join {{ ref('kpi_item_sales') }} k
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
