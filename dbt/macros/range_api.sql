{#
  Parameterized range API: the KPI layer for arbitrary date windows.

  The precomputed `kpi_*` models answer five fixed questions very well, because
  every one is built at (period_label, key) grain against `dim_period`. That
  grain is exactly what makes an arbitrary window impossible: there is no row
  for "March 3rd to April 11th" and there never can be.

  Rather than move the aggregation into the dashboard — which would break the
  rule that every number on screen comes from a tested warehouse object — the
  same SQL is published as parameterized warehouse objects taking (start, end).
  The dashboard passes two dates and renders rows; it still computes nothing.

  Each relation below is defined once, as a body plus the columns it returns,
  and published in whatever form the target warehouse has for "a relation you
  pass arguments to":

    DuckDB     a table macro,  called as `rp_x(a, b)`
    Snowflake  a SQL UDTF,     called as `table(rp_x(a, b))`

  Those are one object under two spellings, with one difference worth knowing:
  Snowflake requires the returned columns to be declared, and rejects
  `returns table ()` rather than inferring them. That is why each relation
  carries a signature next to its body. It is not pure overhead — the
  declaration is a contract, so a body that stops producing what it promises
  fails at build time on Snowflake, where DuckDB would simply publish the new
  shape and let the dashboard find out.

  The honesty guarantee is the equivalence test in
  tests/assert_range_macros_match_periods.sql: for each of the five canonical
  windows, calling the relation with that window's bounds must reproduce the
  precomputed model's numbers exactly. Custom ranges therefore cannot silently
  drift away from the presets — if these two ways of computing a KPI ever
  disagree, the build fails. That test runs on both warehouses.

  Prior-window semantics match dim_period exactly: the prior window is the
  equivalent span immediately before the selected one, and comparisons are
  NULL rather than zero when the extract does not fully cover it.
#}

{#-
  Where these relations live.

  dbt sets no current schema on the Snowflake session, so an unqualified
  `create function` fails with "This session does not have a current schema"
  — and an unqualified call would not resolve either. Both need the marts
  schema spelled out, and `anchor` is whichever relation the caller can see it
  through: `this` while the post-hook is publishing them, and a ref to
  rp_range_api from anywhere else. It cannot be a ref in both places, because
  the publishing model resolving a ref to itself is a graph cycle.

  DuckDB keeps the bare name it has always used: its macros are created in the
  connection's current schema, which is already the right one.
-#}
{% macro range_relation(name, anchor) %}
  {{ return(adapter.dispatch('range_relation', 'retailpulse')(name, anchor)) }}
{% endmacro %}

{% macro default__range_relation(name, anchor) -%}
{{ name }}
{%- endmacro %}

{% macro snowflake__range_relation(name, anchor) -%}
{{ anchor.database }}.{{ anchor.schema }}.{{ name }}
{%- endmacro %}

{% macro bigquery__range_relation(name, anchor) -%}
{#- Backticked as one path: GCP project ids routinely contain hyphens, which
    are otherwise parsed as subtraction. -#}
`{{ anchor.database }}.{{ anchor.schema }}.{{ name }}`
{%- endmacro %}


{#-
  Publish one parameterized relation. Both forms take two DATE arguments and
  return a table; only the syntax and the explicit column list differ.
-#}
{% macro publish_range_relation(name, returns, body) %}
  {{ return(adapter.dispatch('publish_range_relation', 'retailpulse')(name, returns, body)) }}
{% endmacro %}

{% macro default__publish_range_relation(name, returns, body) %}
create or replace macro {{ range_relation(name, this) }}(p_start, p_end) as table
{{ body }};
{% endmacro %}

{% macro snowflake__publish_range_relation(name, returns, body) %}
create or replace function {{ range_relation(name, this) }}(p_start date, p_end date)
returns table ({{ returns }})
as $${{ body }}$$;
{% endmacro %}

{% macro bigquery__publish_range_relation(name, returns, body) %}
{#- BigQuery's table function is the easiest of the three: it infers the
    returned columns from the body, so the declared signature is not needed
    here (it stays declared for Snowflake, which does need it). -#}
create or replace table function {{ range_relation(name, this) }}(
    p_start date, p_end date)
as (
{{ body }}
);
{% endmacro %}


{#-
  How the shared window arithmetic is reached from inside another body. Every
  relation except rp_window itself begins with it.

  DuckDB calls the published macro. Snowflake inlines the same SQL instead,
  because a UDTF that calls another UDTF can only be invoked with constant
  arguments: correlate the outer call to a table's rows -- which is exactly
  what the equivalence test does, and what any per-row use would do -- and the
  planner gives up with "Unsupported subquery type cannot be evaluated". One
  level of correlated table function is fine; two is not.

  So rp_window is still published as a callable relation on both warehouses,
  and is still the single definition of the window arithmetic, but on Snowflake
  its callers embed that definition rather than call through it.
-#}
{% macro rp_window_call(anchor) %}
  {{ return(adapter.dispatch('rp_window_call', 'retailpulse')(anchor)) }}
{% endmacro %}

{% macro default__rp_window_call(anchor) -%}
rp_window(p_start, p_end)
{%- endmacro %}

{% macro snowflake__rp_window_call(anchor) -%}
({{ rp_window_body(anchor) }})
{%- endmacro %}

{% macro bigquery__rp_window_call(anchor) -%}
{{ range_relation('rp_window', anchor) }}(p_start, p_end)
{%- endmacro %}


{#-
  The ORDER BY that ends most of these bodies — on DuckDB only.

  Snowflake accepts `order by` inside a UDTF when the function is called with
  constant arguments, and then refuses the same function when it is called
  against a table's rows: "Unsupported subquery type cannot be evaluated". The
  clause is what makes the body undecorrelatable, and nothing else about it.

  No result is lost by dropping it, because a table function does not promise
  ordered output on either warehouse — anything that needs a specific order has
  to say so at the call site. It stays on DuckDB only because the dashboard's
  queries were written against macros that happen to return sorted rows, and
  quietly reordering its tables is not part of this change.
-#}
{% macro range_order_by(expr) %}
  {{ return(adapter.dispatch('range_order_by', 'retailpulse')(expr)) }}
{% endmacro %}

{% macro default__range_order_by(expr) -%}
order by {{ expr }}
{%- endmacro %}

{% macro snowflake__range_order_by(expr) -%}
{%- endmacro %}


{#-
  How a relation is called from an ordinary query.
-#}
{% macro range_call(name, start_expr, end_expr) %}
  {{ return(adapter.dispatch('range_call', 'retailpulse')(name, start_expr, end_expr)) }}
{% endmacro %}

{% macro default__range_call(name, start_expr, end_expr) -%}
{{ name }}({{ start_expr }}, {{ end_expr }})
{%- endmacro %}

{% macro snowflake__range_call(name, start_expr, end_expr) -%}
table({{ range_relation(name, ref('rp_range_api')) }}({{ start_expr }}, {{ end_expr }}))
{%- endmacro %}

{% macro bigquery__range_call(name, start_expr, end_expr) -%}
{{ range_relation(name, ref('rp_range_api')) }}({{ start_expr }}, {{ end_expr }})
{%- endmacro %}


{#-
  The five canonical windows, read out of dim_period at compile time.

  The equivalence test needs each relation evaluated once per canonical window.
  The obvious way to write that is a correlated call — join dim_period to the
  relation and let the bounds come from each row — and it is how this test used
  to be written. It does not survive contact with Snowflake: a UDTF whose body
  joins two of its own aggregated subqueries (which is most of them, since a
  percent-of-total needs the total) cannot be invoked per row at all. The
  planner reports "Unsupported subquery type cannot be evaluated" and there is
  no rewrite of the call that avoids it.

  So the bounds are resolved here instead, and each relation is called five
  times with constant dates. Same comparison, same five windows, and it now
  runs on both warehouses rather than only the one whose planner cooperates.

  Empty during parsing (`execute` is false and dim_period may not exist yet);
  the test emits a trivially-passing query in that case.
-#}
{% macro canonical_windows() %}
  {%- if not execute -%}
    {{ return([]) }}
  {%- endif -%}
  {%- set results = run_query(
        "select period_label, period_start, period_end, period_days from "
        ~ ref('dim_period') ~ " order by period_order") -%}
  {{ return(results.rows) }}
{% endmacro %}


{#-
  One relation evaluated over every canonical window, labelled and unioned —
  the shape the equivalence test compares against the precomputed models.
-#}
{% macro range_over_periods(name, windows, with_period_days=false) -%}
{%- for w in windows %}
{% if not loop.first %}union all {% endif %}select
    cast('{{ w[0] }}' as {{ string_type() }}) as period_label,
    {%- if with_period_days %}
    cast({{ 'null' if w[3] is none else w[3] }} as integer) as period_days,
    {%- endif %}
    r.*
from {{ range_call(name, "cast('" ~ w[1] ~ "' as date)", "cast('" ~ w[2] ~ "' as date)") }} r
{%- endfor %}
{%- endmacro %}


{#-
  Rebuild every relation. Called from rp_range_api's post-hook so that dbt owns
  their lifecycle: they are recreated on every build, from SQL in version
  control, after the facts they read have been built.
-#}
{% macro create_range_macros() %}
{% for r in [
    ('rp_window',              rp_window_returns(),              rp_window_body(this)),
    ('rp_summary_range',       rp_summary_range_returns(),       rp_summary_range_body(this)),
    ('rp_category_range',      rp_category_range_returns(),      rp_category_range_body(this)),
    ('rp_weekday_range',       rp_weekday_range_returns(),       rp_weekday_range_body(this)),
    ('rp_hour_range',          rp_hour_range_returns(),          rp_hour_range_body(this)),
    ('rp_popular_times_range', rp_popular_times_range_returns(), rp_popular_times_range_body(this)),
    ('rp_payments_range',      rp_payments_range_returns(),      rp_payments_range_body(this)),
    ('rp_items_range',         rp_items_range_returns(),         rp_items_range_body(this))
] %}
{{ publish_range_relation(r[0], r[1], r[2]) }}
{% endfor %}
{% endmacro %}


{#-
  Shared window arithmetic.

  Returns the selected window, the equivalent prior window, and whether the
  extract actually covers that prior window. Every macro below starts here so
  that "the previous 30 days" means one thing in exactly one place.
-#}
{% macro rp_window_returns() %}period_start date, period_end date, period_days number,
        prior_start date, prior_end date, prior_window_complete boolean{% endmacro %}

{% macro rp_window_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
with bounds as (
    select
        cast(p_start as date) as period_start,
        cast(p_end as date) as period_end,
        -- Subtracting one date from another is the one piece of arithmetic
        -- here with no portable operator: DuckDB yields BIGINT and then has no
        -- DATE - BIGINT operator to consume it, and BigQuery yields an
        -- INTERVAL, which will not add to an integer. Shifting a date BY an
        -- integer is fine everywhere; measuring the gap between two needs the
        -- macro. Cast to INTEGER so the window arithmetic below still binds.
        cast({{ date_diff_in('day', 'cast(p_start as date)', 'cast(p_end as date)') }} + 1
             as integer) as period_days
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
cross join history h
{% endmacro %}


{#-
  Headline KPIs. Mirrors kpi_summary.
-#}
{% macro rp_summary_range_returns() %}period_start date, period_end date, period_days number,
        prior_start date, prior_end date,
        orders number, units number(38, 4),
        gross_sales_cents number, discount_cents number,
        tax_cents number, net_sales_cents number,
        avg_order_value_cents number, units_per_order number(38, 2),
        collected_cents number, processing_fee_cents number,
        prior_window_complete boolean,
        prior_net_sales_cents number, prior_orders number, prior_units number(38, 4),
        net_sales_change_pct number(38, 1), orders_change_pct number(38, 1),
        units_change_pct number(38, 1),
        cogs_cents number, gross_profit_cents number,
        gross_margin_pct number(38, 2), cost_coverage_pct number(38, 1){% endmacro %}

{% macro rp_summary_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
{%- set fact_payment = ref('fact_payment') -%}
{%- set fact_margin = ref('fact_order_line_margin') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
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
cross join current_margin m
{% endmacro %}


{#-
  Sales by category. Mirrors kpi_sales_by_category.
-#}
{% macro rp_category_range_returns() %}category_name varchar, orders number, units number(38, 4),
        net_sales_cents number, pct_of_net_sales number(38, 2),
        prior_net_sales_cents number, net_sales_change_pct number(38, 1){% endmacro %}

{% macro rp_category_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
{%- set dim_item = ref('dim_item') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
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
{{ range_order_by('c.net_sales_cents desc') }}
{% endmacro %}


{#-
  Sales by weekday. Mirrors kpi_sales_by_weekday.
-#}
{% macro rp_weekday_range_returns() %}day_of_week number, day_name varchar, is_weekend boolean,
        orders number, net_sales_cents number, avg_order_value_cents number{% endmacro %}

{% macro rp_weekday_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
-- The weekday fields are projected first and aggregated second, rather than
-- repeating the expression in both the SELECT list and the GROUP BY. DuckDB
-- and Snowflake accept the repeated form and let `... in (6, 7)` ride along
-- ungrouped; BigQuery rejects it ("references l.sale_date which is neither
-- grouped nor aggregated"), and is right to.
lines as (
    select
        -- Derived from sale_date, which is already the store's local day.
        -- Reading the weekday off the UTC timestamp is the bug that put
        -- Friday evening's trade on Saturday for a year.
        {{ day_of_week_iso('l.sale_date') }} as day_of_week,
        {{ day_name('l.sale_date') }} as day_name,
        l.square_order_id,
        l.net_sales_cents
    from w cross join {{ fact_order_line }} l
    where l.sale_date is not null
      and l.sale_date between w.period_start and w.period_end
)
select
    day_of_week,
    day_name,
    day_of_week in (6, 7) as is_weekend,
    count(distinct square_order_id) as orders,
    sum(net_sales_cents) as net_sales_cents,
    case when count(distinct square_order_id) > 0
         then round(sum(net_sales_cents) * 1.0 / count(distinct square_order_id))
    end as avg_order_value_cents
from lines
group by day_of_week, day_name
{{ range_order_by('day_of_week') }}
{% endmacro %}


{#-
  Sales by hour of day. Mirrors kpi_sales_by_hour.
-#}
{% macro rp_hour_range_returns() %}hour_of_day number, orders number, net_sales_cents number{% endmacro %}

{% macro rp_hour_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
with w as (select * from {{ rp_window_call(anchor) }})
select
    cast(extract(hour from l.closed_at_local) as integer) as hour_of_day,
    count(distinct l.square_order_id) as orders,
    sum(l.net_sales_cents) as net_sales_cents
from w cross join {{ fact_order_line }} l
where l.sale_date is not null
  and l.sale_date between w.period_start and w.period_end
group by hour_of_day
{{ range_order_by('hour_of_day') }}
{% endmacro %}


{#-
  Busyness by hour, per weekday — the "popular times" grid.

  The separate weekday and hour-of-day views each answer half a question. A
  shopkeeper deciding when to put a second person on the till needs the other
  half: Friday at 5pm is not Tuesday at 5pm. This is the cross-tab, plus
  `busyness_pct` — each hour as a share of that weekday's own busiest hour, so
  a quiet Monday is still readable on the same scale as a heaving Saturday.

  `days_observed` is published because it is the honest caveat: a 7-day window
  gives exactly one of each weekday, and an average over one observation is
  just that day.
-#}
{% macro rp_popular_times_range_returns() %}day_of_week number, day_name varchar, hour_of_day number,
        days_observed number, orders number, net_sales_cents number,
        orders_per_day number(38, 1), busyness_pct number{% endmacro %}

{% macro rp_popular_times_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
lines as (
    select
        l.sale_date,
        {{ day_of_week_iso('l.sale_date') }} as day_of_week,
        {{ day_name('l.sale_date') }} as day_name,
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
{{ range_order_by('s.day_of_week, s.hour_of_day') }}
{% endmacro %}


{#-
  Payment methods. Mirrors kpi_payment_methods.
-#}
{% macro rp_payments_range_returns() %}source_type varchar, payments number,
        amount_collected_cents number, processing_fee_cents number,
        pct_of_collected number(38, 2){% endmacro %}

{% macro rp_payments_range_body(anchor) %}
{%- set fact_payment = ref('fact_payment') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
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
{{ range_order_by('c.amount_collected_cents desc') }}
{% endmacro %}


{#-
  Per-item sales. Mirrors kpi_item_sales.

  units_all_time / first_sold_at / last_sold_at are deliberately whole-history
  figures, not window figures — they answer "is this item still alive at all",
  which a window cannot.
-#}
{% macro rp_items_range_returns() %}variation_id varchar, item_name varchar, variation_name varchar,
        category_name varchar, is_lottery boolean,
        units number(38, 4), orders number, net_sales_cents number,
        avg_weekly_units number(38, 2),
        prior_units number(38, 4), prior_net_sales_cents number,
        units_change_pct number(38, 1), net_sales_change_pct number(38, 1),
        units_all_time number(38, 4), first_sold_at date, last_sold_at date{% endmacro %}

{% macro rp_items_range_body(anchor) %}
{%- set fact_order_line = ref('fact_order_line') -%}
{%- set dim_item = ref('dim_item') -%}
with w as (select * from {{ rp_window_call(anchor) }}),
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
{{ range_order_by('c.net_sales_cents desc') }}
{% endmacro %}
