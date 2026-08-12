-- fact_order_line must still contain exactly what staging contains.
--
-- This is the test that makes the incremental build trustworthy. A
-- full-refresh model cannot drift from its source: it is rebuilt from that
-- source every time. An incremental one can, in three directions, and none of
-- them raises an error on its own:
--
--   * rows MISSED, when a change lands outside the lookback window
--   * rows DUPLICATED, if the surrogate key ever stops being unique per line
--   * rows STALE, left behind after being removed upstream — the known
--     limitation of a merge, which matches on key and so never learns about a
--     row that simply stopped arriving
--
-- Comparing counts and totals against staging catches all three for the price
-- of two aggregates, and does it on every build rather than whenever someone
-- next thinks to check. The join is left-only on dimensions, so the fact table
-- and staging must agree row for row and cent for cent.
--
-- If this fails, the fix is `dbt build --full-refresh --select fact_order_line+`
-- and then working out which of the three happened.
with staged as (
    select
        count(*) as rows_in_source,
        count(distinct order_id || '|' || line_item_uid) as keys_in_source,
        sum(net_sales_cents) as net_sales_in_source,
        sum(quantity) as units_in_source
    from {{ ref('stg_order_lines') }}
),

fact as (
    select
        count(*) as rows_in_fact,
        count(distinct order_line_key) as keys_in_fact,
        sum(net_sales_cents) as net_sales_in_fact,
        sum(quantity) as units_in_fact
    from {{ ref('fact_order_line') }}
)

select
    s.rows_in_source,
    f.rows_in_fact,
    s.keys_in_source,
    f.keys_in_fact,
    s.net_sales_in_source,
    f.net_sales_in_fact
from staged s
cross join fact f
where s.rows_in_source is distinct from f.rows_in_fact
   -- One key per row, on both sides: catches a hash collision or a natural key
   -- that turns out not to be unique, either of which the row count alone
   -- would hide.
   or s.keys_in_source is distinct from f.keys_in_fact
   or f.rows_in_fact is distinct from f.keys_in_fact
   or s.net_sales_in_source is distinct from f.net_sales_in_fact
   or s.units_in_source is distinct from f.units_in_fact
