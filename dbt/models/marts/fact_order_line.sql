{#
  The one incremental model in the project, and the only one that needs to be:
  it is the largest table, everything else is aggregated from it, and a full
  rebuild of the marts takes seconds precisely because this stays cheap.

  The filter is on `order_updated_at`, not on `closed_at`. That distinction is
  the whole problem. Square lets an order be amended after it closes — a
  refund, a corrected price — and such an edit changes a row whose `closed_at`
  is still last Tuesday. The obvious filter,

      where closed_at > (select max(closed_at) from this)

  compiles, runs fast, and never sees that row again. The warehouse quietly
  drifts from the source and nothing errors. Filtering on when the record
  *changed* rather than when the sale *happened* is what makes late-arriving
  edits visible.

  The seven-day lookback on top of that covers the gap between an edit landing
  in Square and the extract that collects it, plus any run that was skipped.
  Widening it costs a little more work per run and buys more tolerance; it is
  a dial, not a constant of nature.

  `merge` is the strategy because it is the only one all three warehouses
  support — BigQuery has no `delete+insert`. It updates rows whose key already
  exists and inserts the rest, which covers amendments and new sales. What it
  does not cover is a line item *removed* from an order: nothing arrives to
  match it, so the old row survives. Square models voids as a state change
  rather than a deletion, so this has not been observed here, and
  `assert_fact_matches_source` compares this table against staging on every
  build so any drift fails loudly rather than accumulating. A periodic
  `dbt build --full-refresh` is the reset.

  `on_schema_change` is set because the default is `ignore`, and ignore means
  adding a column here leaves the existing table alone while the incremental
  query starts selecting it. Adding `order_updated_at` under the default
  failed with "Referenced column order_updated_at not found in FROM clause",
  because the watermark reads a column the target did not have yet. Syncing
  makes dbt alter the target instead of guessing.
#}
{{ config(
    materialized='incremental',
    unique_key='order_line_key',
    incremental_strategy='merge',
    on_schema_change='sync_all_columns',
) }}

{% set lookback_days = 7 %}

with order_lines as (
    select * from {{ ref('stg_order_lines') }}
),

locations as (
    select location_key, square_location_id, timezone from {{ ref('dim_location') }}
),

items as (
    select item_key, square_catalog_object_id from {{ ref('dim_item') }}
)

{%- if is_incremental() %},

-- The oldest change worth reprocessing.
--
-- Everything is reduced to a DATE first, including the max() itself. DuckDB
-- and Snowflake will happily coalesce a TIMESTAMP with a DATE and compare the
-- result; BigQuery refuses both, with "Unable to find common supertype".
-- Day granularity is what a lookback measured in days wants anyway.
watermark as (
    select {{ add_days('coalesce(cast(max(order_updated_at) as date), cast(\'1970-01-01\' as date))',
                       -lookback_days) }} as reprocess_from
    from {{ this }}
)
{%- endif %}

select
    -- A hash of the natural key, not row_number(). See surrogate_key(): a
    -- positional key is only stable while the whole table is rebuilt at once,
    -- which is exactly what an incremental model stops doing.
    {{ surrogate_key(['order_lines.order_id', 'order_lines.line_item_uid']) }} as order_line_key,
    order_lines.order_id as square_order_id,
    order_lines.line_item_uid as square_line_item_uid,
    order_lines.catalog_object_id,
    -- name captured on the line at sale time (survives later catalog edits/deletes)
    order_lines.item_name,
    order_lines.variation_name,
    locations.location_key,
    items.item_key,
    -- closed_at is kept exactly as Square recorded it: a naive timestamp
    -- holding UTC. It stays for lineage and for anyone reconciling against
    -- Square's own API responses.
    order_lines.closed_at,
    -- ...and is converted to the store's wall clock ONCE, here, using the
    -- timezone Square reports for the location. Every downstream model reads
    -- sale_date / closed_at_local instead of re-deriving from UTC.
    --
    -- This matters more than it looks for a liquor store: a 9pm sale is 04:00
    -- the NEXT day in UTC, so deriving dates from UTC pushed a large slice of
    -- every evening's trade onto the following day — and onto the following
    -- weekday. It also put the "when you sell" peak at 22:00–06:00, which is
    -- nobody's trading pattern.
    --
    -- The inner timezone() reads the naive timestamp as UTC and yields an
    -- instant; the outer one renders that instant as wall-clock time in the
    -- store's zone. It is DST-correct, so the offset is -7 in July and -8 in
    -- January rather than a fixed shift. Locations with no timezone on file
    -- fall back to UTC, which is the old behaviour rather than a wrong one.
    {{ to_local_time("coalesce(locations.timezone, 'UTC')", "order_lines.closed_at") }} as closed_at_local,
    cast(
        {{ to_local_time("coalesce(locations.timezone, 'UTC')", "order_lines.closed_at") }} as date
    ) as sale_date,
    -- Published so the incremental watermark can be read back off this table,
    -- and because "when did Square last touch this order" is the column you
    -- want the moment a figure is disputed.
    order_lines.order_updated_at,
    order_lines.quantity,
    order_lines.gross_sales_cents,
    order_lines.discount_cents,
    order_lines.tax_cents,
    order_lines.net_sales_cents
from order_lines
left join locations on order_lines.location_id = locations.square_location_id
-- left join: some seeded/legacy line items reference catalog objects that
-- have since been deleted or recreated, so item_key can legitimately be null.
left join items on order_lines.catalog_object_id = items.square_catalog_object_id

{%- if is_incremental() %}
cross join watermark
-- A null order_updated_at can never satisfy the lower bound, so those rows
-- would be collected on the first build and then never reconsidered. They are
-- few and the merge makes reprocessing them harmless.
where order_lines.order_updated_at is null
   or cast(order_lines.order_updated_at as date) >= watermark.reprocess_from
{%- endif %}
