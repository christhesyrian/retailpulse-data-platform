-- One row per item variation: how much of it has sold, recently and overall.
-- "this week" is the current (partial) ISO week to date; "last week" is the
-- most recent complete week. All windows are relative to current_date.
with lines as (
    select
        -- custom (non-catalog) line items key on their typed name; see
        -- kpi_item_weekly_sales for the rationale.
        coalesce(catalog_object_id, 'custom:' || coalesce(item_name, 'unknown')) as variation_id,
        item_name,
        variation_name,
        square_order_id,
        quantity,
        net_sales_cents,
        cast(closed_at as date) as sale_date
    from {{ ref('fact_order_line') }}
    where closed_at is not null
),

anchors as (
    select
        date_trunc('week', current_date)::date as this_week_start,
        (date_trunc('week', current_date) - interval 7 day)::date as last_week_start,
        (date_trunc('week', current_date) - interval 14 day)::date as prior_week_start,
        date_trunc('month', current_date)::date as this_month_start,
        (date_trunc('month', current_date) - interval 1 month)::date as last_month_start
),

per_item as (
    select
        l.variation_id,
        max(l.item_name) as item_name,
        max(l.variation_name) as variation_name,
        count(distinct l.square_order_id) as orders_total,
        sum(l.quantity) as units_total,
        sum(l.net_sales_cents) as net_sales_cents_total,
        min(l.sale_date) as first_sold_at,
        max(l.sale_date) as last_sold_at,
        sum(l.quantity) filter (where l.sale_date >= a.this_week_start) as units_this_week,
        sum(l.quantity) filter (
            where l.sale_date >= a.last_week_start and l.sale_date < a.this_week_start
        ) as units_last_week,
        sum(l.quantity) filter (
            where l.sale_date >= a.prior_week_start and l.sale_date < a.last_week_start
        ) as units_prior_week,
        sum(l.quantity) filter (where l.sale_date >= a.this_month_start) as units_this_month,
        sum(l.quantity) filter (
            where l.sale_date >= a.last_month_start and l.sale_date < a.this_month_start
        ) as units_last_month
    from lines l
    cross join anchors a
    group by l.variation_id
),

-- trailing 4 COMPLETE weeks average (weeks strictly before the current week)
avg_weekly as (
    select
        w.variation_id,
        round(avg(w.units_sold), 2) as avg_weekly_units_4wk
    from {{ ref('kpi_item_weekly_sales') }} w
    cross join anchors a
    where w.week_start >= (a.this_week_start - interval 4 week)
      and w.week_start < a.this_week_start
    group by w.variation_id
),

categories as (
    select square_catalog_object_id, category_name from {{ ref('dim_item') }}
)

select
    p.variation_id,
    p.item_name,
    p.variation_name,
    c.category_name,
    coalesce(p.units_total, 0) as units_total,
    coalesce(p.orders_total, 0) as orders_total,
    coalesce(p.net_sales_cents_total, 0) as net_sales_cents_total,
    p.first_sold_at,
    p.last_sold_at,
    coalesce(p.units_this_week, 0) as units_this_week,
    coalesce(p.units_last_week, 0) as units_last_week,
    coalesce(p.units_this_month, 0) as units_this_month,
    coalesce(p.units_last_month, 0) as units_last_month,
    coalesce(aw.avg_weekly_units_4wk, 0) as avg_weekly_units_4wk,
    round(
        100.0 * (coalesce(p.units_last_week, 0) - coalesce(p.units_prior_week, 0))
        / nullif(p.units_prior_week, 0), 1
    ) as wow_trend_pct
from per_item p
left join avg_weekly aw on p.variation_id = aw.variation_id
left join categories c on p.variation_id = c.square_catalog_object_id
order by units_total desc
