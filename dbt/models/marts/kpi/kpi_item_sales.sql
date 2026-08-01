-- Per-item sales, one row per (period, item variation).
--
-- This replaces the old fixed this-week/last-week/this-month columns: with a
-- period selector driving the dashboard, "last 7 days vs the 7 before it" is
-- just the Last 7 days row, and the same model answers the 30-, 90- and
-- 365-day questions without a new column per window.
--
-- An item appears in a period only if it sold in it. first_sold_at and
-- last_sold_at are all-time, so a row can show a "last sold" date outside its
-- own window — that is deliberate, it answers "when did this last move?".
with lines as (
    select
        f.sale_date,
        -- custom (non-catalog) line items key on their typed name; see
        -- kpi_item_weekly_sales for the rationale.
        coalesce(f.catalog_object_id, 'custom:' || coalesce(f.item_name, 'unknown'))
            as variation_id,
        f.item_name,
        f.variation_name,
        f.square_order_id,
        f.quantity,
        f.net_sales_cents
    from {{ ref('fact_order_line') }} f
    where f.sale_date is not null
),

periods as (
    select * from {{ ref('dim_period') }}
),

current_window as (
    select
        p.period_label,
        p.period_order,
        l.variation_id,
        max(l.item_name) as item_name,
        max(l.variation_name) as variation_name,
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.period_start and p.period_end
    group by p.period_label, p.period_order, l.variation_id
),

prior_window as (
    select
        p.period_label,
        l.variation_id,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from periods p
    join lines l on l.sale_date between p.prior_start and p.prior_end
    group by p.period_label, l.variation_id
),

lifetime as (
    select
        variation_id,
        min(sale_date) as first_sold_at,
        max(sale_date) as last_sold_at,
        sum(quantity) as units_all_time
    from lines
    group by variation_id
),

categories as (
    select square_catalog_object_id, category_name from {{ ref('dim_item') }}
)

select
    c.period_label,
    c.period_order,
    c.variation_id,
    c.item_name,
    c.variation_name,
    cat.category_name,
    c.units,
    c.orders,
    c.net_sales_cents,
    -- Average units per week inside the window, so a 7-day and a 90-day view
    -- are directly comparable. Null for All time, whose length depends on how
    -- much history was extracted rather than on the period definition.
    case
        when p.period_days is not null
            then round(c.units * 7.0 / p.period_days, 2)
    end as avg_weekly_units,
    -- Suppressed unless the extract fully covers the prior window; see
    -- dim_period.prior_window_complete.
    case when p.prior_window_complete then pr.units end as prior_units,
    case when p.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when p.prior_window_complete then
        round(100.0 * (c.units - pr.units) / nullif(pr.units, 0), 1)
    end as units_change_pct,
    case when p.prior_window_complete then round(
        100.0 * (c.net_sales_cents - pr.net_sales_cents) / nullif(pr.net_sales_cents, 0), 1
    ) end as net_sales_change_pct,
    lt.units_all_time,
    lt.first_sold_at,
    lt.last_sold_at
from current_window c
join periods p on p.period_label = c.period_label
left join prior_window pr
    on pr.period_label = c.period_label
   and pr.variation_id = c.variation_id
left join lifetime lt on lt.variation_id = c.variation_id
left join categories cat on cat.square_catalog_object_id = c.variation_id
order by c.period_order, c.units desc
