{#
  Parameterized range macros: the KPI layer for arbitrary date windows.

  The precomputed `kpi_*` models answer five fixed questions very well, because
  every one is built at (period_label, key) grain against `dim_period`. That
  grain is exactly what makes an arbitrary window impossible: there is no row
  for "March 3rd to April 11th" and there never can be.

  Rather than move the aggregation into the dashboard — which would break the
  rule that every number on screen comes from a tested warehouse object — the
  same SQL is published as DuckDB *table macros* taking (start, end). The
  dashboard passes two dates and renders rows; it still computes nothing.

  The honesty guarantee is the equivalence test in
  tests/assert_range_macros_match_periods.sql: for each of the five canonical
  windows, calling the macro with that window's bounds must reproduce the
  precomputed model's numbers exactly. Custom ranges therefore cannot silently
  drift away from the presets — if these two ways of computing a KPI ever
  disagree, the build fails.

  Prior-window semantics match dim_period exactly: the prior window is the
  equivalent span immediately before the selected one, and comparisons are
  NULL rather than zero when the extract does not fully cover it.
#}

{% macro create_range_macros() %}

{% set fact_order_line = ref('fact_order_line') %}
{% set fact_payment = ref('fact_payment') %}
{% set fact_margin = ref('fact_order_line_margin') %}
{% set dim_item = ref('dim_item') %}

--------------------------------------------------------------------------
-- Shared window arithmetic.
--
-- Returns the selected window, the equivalent prior window, and whether the
-- extract actually covers that prior window. Every macro below starts here so
-- that "the previous 30 days" means one thing in exactly one place.
--------------------------------------------------------------------------
create or replace macro rp_window(p_start, p_end) as table
with bounds as (
    select
        cast(p_start as date) as period_start,
        cast(p_end as date) as period_end,
        -- Cast to INTEGER: date subtraction yields BIGINT, and DuckDB has no
        -- DATE - BIGINT operator, so the window arithmetic below fails to bind.
        cast(cast(p_end as date) - cast(p_start as date) + 1 as integer) as period_days
),
history as (
    select coalesce(min(sale_date), current_date) as first_sale_date
    from {{ fact_order_line }}
    where sale_date is not null
)
select
    b.period_start,
    b.period_end,
    b.period_days,
    b.period_start - b.period_days as prior_start,
    b.period_start - 1 as prior_end,
    -- The same guard dim_period applies: comparing against a window the
    -- extract only partly covers produces a real-looking but meaningless
    -- number (a 90-day view against 2 days of prior data reads as +4,659%).
    (b.period_start - b.period_days) >= h.first_sale_date as prior_window_complete
from bounds b
cross join history h;

--------------------------------------------------------------------------
-- Headline KPIs. Mirrors kpi_summary.
--------------------------------------------------------------------------
create or replace macro rp_summary_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end)),
lines as (
    select sale_date, order_line_key, square_order_id, quantity,
           gross_sales_cents, discount_cents, tax_cents, net_sales_cents
    from {{ fact_order_line }}
    where sale_date is not null
),
current_lines as (
    select
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.gross_sales_cents) as gross_sales_cents,
        sum(l.discount_cents) as discount_cents,
        sum(l.tax_cents) as tax_cents,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.period_start and w.period_end
),
prior_lines as (
    select
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.prior_start and w.prior_end
),
current_payments as (
    select
        sum(y.amount_cents) as collected_cents,
        sum(y.processing_fee_cents) as processing_fee_cents
    from w cross join {{ fact_payment }} y
    where y.pay_date between w.period_start and w.period_end
),
current_margin as (
    select
        sum(case when m.has_cost then m.net_sales_cents end) as costed_net_sales_cents,
        sum(m.cogs_cents) as cogs_cents,
        sum(m.gross_profit_cents) as gross_profit_cents,
        sum(m.net_sales_cents) as all_net_sales_cents
    from w
    cross join {{ fact_margin }} m
    join lines l on m.order_line_key = l.order_line_key
    where l.sale_date between w.period_start and w.period_end
)
select
    w.period_start,
    w.period_end,
    w.period_days,
    -- Published so the dashboard can name the window it is comparing against
    -- without recomputing the offset itself.
    w.prior_start,
    w.prior_end,
    coalesce(c.orders, 0) as orders,
    coalesce(c.units, 0) as units,
    coalesce(c.gross_sales_cents, 0) as gross_sales_cents,
    coalesce(c.discount_cents, 0) as discount_cents,
    coalesce(c.tax_cents, 0) as tax_cents,
    coalesce(c.net_sales_cents, 0) as net_sales_cents,
    case when coalesce(c.orders, 0) > 0
         then round(c.net_sales_cents * 1.0 / c.orders) else 0 end as avg_order_value_cents,
    case when coalesce(c.orders, 0) > 0
         then round(c.units * 1.0 / c.orders, 2) else 0 end as units_per_order,
    coalesce(y.collected_cents, 0) as collected_cents,
    coalesce(y.processing_fee_cents, 0) as processing_fee_cents,
    w.prior_window_complete,
    case when w.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when w.prior_window_complete then pr.orders end as prior_orders,
    case when w.prior_window_complete then pr.units end as prior_units,
    case when w.prior_window_complete then round(
        100.0 * (coalesce(c.net_sales_cents, 0) - pr.net_sales_cents)
        / nullif(pr.net_sales_cents, 0), 1) end as net_sales_change_pct,
    case when w.prior_window_complete then round(
        100.0 * (coalesce(c.orders, 0) - pr.orders)
        / nullif(pr.orders, 0), 1) end as orders_change_pct,
    case when w.prior_window_complete then round(
        100.0 * (coalesce(c.units, 0) - pr.units)
        / nullif(pr.units, 0), 1) end as units_change_pct,
    m.cogs_cents,
    m.gross_profit_cents,
    case when m.costed_net_sales_cents > 0
         then round(100.0 * m.gross_profit_cents / m.costed_net_sales_cents, 2)
    end as gross_margin_pct,
    case when m.all_net_sales_cents > 0
         then round(100.0 * m.costed_net_sales_cents / m.all_net_sales_cents, 1)
    end as cost_coverage_pct
from w
cross join current_lines c
cross join prior_lines pr
cross join current_payments y
cross join current_margin m;

--------------------------------------------------------------------------
-- Sales by category. Mirrors kpi_sales_by_category.
--------------------------------------------------------------------------
create or replace macro rp_category_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end)),
lines as (
    -- Category lives on dim_item, joined on the item surrogate key, and
    -- unmatched lines are labelled rather than dropped — exactly as
    -- kpi_sales_by_category does it.
    select
        f.sale_date,
        f.square_order_id,
        f.quantity,
        f.net_sales_cents,
        coalesce(i.category_name, 'Uncategorized') as category_name
    from {{ fact_order_line }} f
    left join {{ dim_item }} i on f.item_key = i.item_key
    where f.sale_date is not null
),
current_rows as (
    select
        l.category_name,
        count(distinct l.square_order_id) as orders,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.period_start and w.period_end
    group by l.category_name
),
prior_rows as (
    select
        l.category_name,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.prior_start and w.prior_end
    group by l.category_name
),
total as (select sum(net_sales_cents) as net_sales_cents from current_rows)
select
    c.category_name,
    c.orders,
    c.units,
    c.net_sales_cents,
    case when t.net_sales_cents > 0
         then round(100.0 * c.net_sales_cents / t.net_sales_cents, 2) end as pct_of_net_sales,
    case when w.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when w.prior_window_complete then round(
        100.0 * (c.net_sales_cents - pr.net_sales_cents)
        / nullif(pr.net_sales_cents, 0), 1) end as net_sales_change_pct
from current_rows c
cross join total t
cross join w
left join prior_rows pr on pr.category_name = c.category_name
order by c.net_sales_cents desc;

--------------------------------------------------------------------------
-- Sales by weekday. Mirrors kpi_sales_by_weekday.
--------------------------------------------------------------------------
create or replace macro rp_weekday_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end))
select
    -- Derived from sale_date, which is already the store's local day. Reading
    -- the weekday off the UTC timestamp is the bug that put Friday evening's
    -- trade on Saturday for a year.
    isodow(l.sale_date) as day_of_week,
    dayname(l.sale_date) as day_name,
    isodow(l.sale_date) in (6, 7) as is_weekend,
    count(distinct l.square_order_id) as orders,
    sum(l.net_sales_cents) as net_sales_cents,
    case when count(distinct l.square_order_id) > 0
         then round(sum(l.net_sales_cents) * 1.0 / count(distinct l.square_order_id))
    end as avg_order_value_cents
from w cross join {{ fact_order_line }} l
where l.sale_date is not null
  and l.sale_date between w.period_start and w.period_end
group by isodow(l.sale_date), dayname(l.sale_date)
order by day_of_week;

--------------------------------------------------------------------------
-- Sales by hour of day. Mirrors kpi_sales_by_hour.
--------------------------------------------------------------------------
create or replace macro rp_hour_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end))
select
    cast(extract(hour from l.closed_at_local) as integer) as hour_of_day,
    count(distinct l.square_order_id) as orders,
    sum(l.net_sales_cents) as net_sales_cents
from w cross join {{ fact_order_line }} l
where l.sale_date is not null
  and l.sale_date between w.period_start and w.period_end
group by hour_of_day
order by hour_of_day;

--------------------------------------------------------------------------
-- Busyness by hour, per weekday — the "popular times" grid.
--
-- The separate weekday and hour-of-day views each answer half a question. A
-- shopkeeper deciding when to put a second person on the till needs the other
-- half: Friday at 5pm is not Tuesday at 5pm. This is the cross-tab, plus
-- `busyness_pct` — each hour as a share of that weekday's own busiest hour, so
-- a quiet Monday is still readable on the same scale as a heaving Saturday.
--
-- `days_observed` is published because it is the honest caveat: a 7-day window
-- gives exactly one of each weekday, and an average over one observation is
-- just that day.
--------------------------------------------------------------------------
create or replace macro rp_popular_times_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end)),
lines as (
    select
        l.sale_date,
        isodow(l.sale_date) as day_of_week,
        dayname(l.sale_date) as day_name,
        cast(extract(hour from l.closed_at_local) as integer) as hour_of_day,
        l.square_order_id,
        l.net_sales_cents
    from w cross join {{ fact_order_line }} l
    where l.sale_date is not null
      and l.sale_date between w.period_start and w.period_end
),
per_slot as (
    select
        day_of_week,
        day_name,
        hour_of_day,
        count(distinct sale_date) as days_observed,
        count(distinct square_order_id) as orders,
        sum(net_sales_cents) as net_sales_cents
    from lines
    group by day_of_week, day_name, hour_of_day
),
peak as (
    select day_of_week, max(orders) as peak_orders
    from per_slot
    group by day_of_week
)
select
    s.day_of_week,
    s.day_name,
    s.hour_of_day,
    s.days_observed,
    s.orders,
    s.net_sales_cents,
    -- Orders per occurrence of that weekday: the figure that means "a typical
    -- Friday at 5pm", rather than a total that grows with the window length.
    round(s.orders * 1.0 / nullif(s.days_observed, 0), 1) as orders_per_day,
    round(100.0 * s.orders / nullif(p.peak_orders, 0)) as busyness_pct
from per_slot s
join peak p on p.day_of_week = s.day_of_week
order by s.day_of_week, s.hour_of_day;

--------------------------------------------------------------------------
-- Payment methods. Mirrors kpi_payment_methods.
--------------------------------------------------------------------------
create or replace macro rp_payments_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end)),
current_rows as (
    select
        y.source_type,
        count(*) as payments,
        sum(y.amount_cents) as amount_collected_cents,
        sum(y.processing_fee_cents) as processing_fee_cents
    from w cross join {{ fact_payment }} y
    where y.pay_date between w.period_start and w.period_end
    group by y.source_type
),
total as (select sum(amount_collected_cents) as amount_collected_cents from current_rows)
select
    c.source_type,
    c.payments,
    c.amount_collected_cents,
    c.processing_fee_cents,
    case when t.amount_collected_cents > 0
         then round(100.0 * c.amount_collected_cents / t.amount_collected_cents, 2)
    end as pct_of_collected
from current_rows c
cross join total t
order by c.amount_collected_cents desc;

--------------------------------------------------------------------------
-- Per-item sales. Mirrors kpi_item_sales.
--
-- units_all_time / first_sold_at / last_sold_at are deliberately whole-history
-- figures, not window figures — they answer "is this item still alive at all",
-- which a window cannot.
--------------------------------------------------------------------------
create or replace macro rp_items_range(p_start, p_end) as table
with w as (select * from rp_window(p_start, p_end)),
lines as (
    select
        f.sale_date,
        -- Custom (non-catalog) line items key on their typed name, matching
        -- kpi_item_sales; without this they all collapse into one null key.
        coalesce(f.catalog_object_id, 'custom:' || coalesce(f.item_name, 'unknown'))
            as variation_id,
        f.item_name,
        f.variation_name,
        f.square_order_id,
        f.quantity,
        f.net_sales_cents
    from {{ fact_order_line }} f
    where f.sale_date is not null
),
current_rows as (
    select
        l.variation_id,
        max(l.item_name) as item_name,
        max(l.variation_name) as variation_name,
        sum(l.quantity) as units,
        count(distinct l.square_order_id) as orders,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.period_start and w.period_end
    group by l.variation_id
),
prior_rows as (
    select
        l.variation_id,
        sum(l.quantity) as units,
        sum(l.net_sales_cents) as net_sales_cents
    from w cross join lines l
    where l.sale_date between w.prior_start and w.prior_end
    group by l.variation_id
),
lifetime as (
    select
        variation_id,
        sum(quantity) as units_all_time,
        min(sale_date) as first_sold_at,
        max(sale_date) as last_sold_at
    from lines
    group by variation_id
),
categories as (
    select square_catalog_object_id, category_name, is_lottery from {{ dim_item }}
)
select
    c.variation_id,
    c.item_name,
    c.variation_name,
    cat.category_name,
    -- Unmatched items (custom line items with no catalog entry) are not
    -- lottery; coalesce rather than leaving a null that would drop them from
    -- both sides of a `where is_lottery` split.
    coalesce(cat.is_lottery, false) as is_lottery,
    c.units,
    c.orders,
    c.net_sales_cents,
    -- Units per week inside the window, so a 7-day and a 90-day view are
    -- directly comparable. A custom range always has a definite length, so
    -- unlike kpi_item_sales (where "All time" is null) this is always defined.
    round(c.units * 7.0 / nullif(w.period_days, 0), 2) as avg_weekly_units,
    case when w.prior_window_complete then pr.units end as prior_units,
    case when w.prior_window_complete then pr.net_sales_cents end as prior_net_sales_cents,
    case when w.prior_window_complete then round(
        100.0 * (c.units - pr.units) / nullif(pr.units, 0), 1) end as units_change_pct,
    case when w.prior_window_complete then round(
        100.0 * (c.net_sales_cents - pr.net_sales_cents)
        / nullif(pr.net_sales_cents, 0), 1) end as net_sales_change_pct,
    lt.units_all_time,
    lt.first_sold_at,
    lt.last_sold_at
from current_rows c
cross join w
left join prior_rows pr on pr.variation_id = c.variation_id
left join lifetime lt on lt.variation_id = c.variation_id
left join categories cat on cat.square_catalog_object_id = c.variation_id
order by c.net_sales_cents desc;

{% endmacro %}
