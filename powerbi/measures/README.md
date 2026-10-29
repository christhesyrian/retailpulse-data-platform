# DAX measures, mapped to their dbt counterparts

Every measure in [`core.dax`](core.dax) has an equivalent in
`dbt/models/marts/kpi/`. That duplication is deliberate and it is the point:
two engines computing the same figures from the same rows is a *testable*
claim, and `scripts/compare_dax_to_dbt.py` is meant to test it the way
`scripts/compare_warehouses.py` already tests that three warehouses agree —
not that both ran, but that both got the same answer.

A measure that disagrees with its SQL counterpart is almost always a
filter-context mistake: a missing `REMOVEFILTERS`, a relationship pointing the
wrong way, a `SUM` where the SQL had `COUNT(DISTINCT …)`. Those produce a
plausible number rather than an error, which is why they need a test rather
than a review.

## The mapping

| DAX measure | dbt model | Column |
|---|---|---|
| `Net Sales Cents` / `Net Sales` | `kpi_summary` | `net_sales_cents` |
| `Orders` | `kpi_summary` | `orders` |
| `Units` | `kpi_summary` | `units` |
| `Gross Sales` | `kpi_summary` | `gross_sales_cents` |
| `Discounts` | `kpi_summary` | `discount_cents` |
| `Tax` | `kpi_summary` | `tax_cents` |
| `AOV Cents` / `AOV` | `kpi_summary` | `avg_order_value_cents` |
| `Units per Order` | `kpi_summary` | `units_per_order` |
| `Collected Cents` / `Collected` | `kpi_summary` | `collected_cents` |
| `Processing Fees` | `kpi_summary` | `processing_fee_cents` |
| `Net Sales PP` | `kpi_summary` | `prior_net_sales_cents` |
| `Orders PP` | `kpi_summary` | `prior_orders` |
| `Net Sales Change %` | `kpi_summary` | `net_sales_change_pct` |
| `Net Sales % of Total` | `kpi_sales_by_category` | `pct_of_net_sales` |
| `Collected % of Total` | `kpi_payment_methods` | `pct_of_collected` |
| `Net Sales 7d Avg` | `kpi_daily_sales` | `net_sales_7d_avg_cents` |

Four models need **no measure of their own at all** —
`kpi_sales_by_category`, `kpi_sales_by_weekday`, `kpi_sales_by_hour` and
`kpi_payment_methods`. Each is `[Net Sales]` and `[Orders]` with a different
column on the axis. That is the whole asymmetry: five period-aware SQL models
at `(period_label, key)` grain, roughly a dozen measures, and the grouping
arrives from the visual rather than from a `GROUP BY`.

## Three reasons the numbers will not match on the first attempt

Each of these is a real discrepancy with a known size, not a rounding worry.

**Blank `sale_date`.** Every `kpi_*` model opens with `where sale_date is not
null`. Square returns a few order lines it never closed, which are genuinely
undated — **19 lines, $228.31, on the production warehouse** (zero on the
synthetic one, where every order closes). DAX does not drop them, and with no
date filter applied they fall into the blank date member and inflate the
total. Exclude them when the fact is loaded, not inside each measure.

**AOV rounding.** `kpi_summary` computes
`round(net_sales_cents * 1.0 / orders)` — whole cents, rounded in SQL, before
anything divides by 100 for display. Dividing in DAX first and rounding after
disagrees in the last decimal on about half the periods. `AOV Cents` rounds in
the same place.

**Change percentages are numbers, not fractions.** `net_sales_change_pct` is
`12.3` meaning 12.3%, rounded to one decimal. `DIVIDE()` alone returns `0.123`.
`Net Sales Change %` multiplies and rounds to match, which means the column
must be formatted as a plain number — formatting it as a percentage multiplies
by 100 a second time.

## What replaces `dim_period`

Nothing imports `dim_period`. Its five fixed windows, their prior equivalents
and `prior_window_complete` are all reproduced by the `*_PP` measures:
`COUNTROWS(VALUES(dim_date[date_day]))` is the selected window's length,
`DATEADD` shifts back by exactly that many days, and the `IF` returns `BLANK`
when the prior window would start before the first day of data.

That last part is the honest bit, and it is a direct translation of a decision
already made in SQL rather than an invention. `prior_window_complete` is
`false` for `Last 365 days` on the production warehouse — 383 days of history
cannot support a year-over-year comparison — and the DAX must return blank
there too, not a number computed from a window the data only half covers.

One edge case where the two reason differently and agree anyway, worth knowing
before it looks like a bug. For `All time`, `dim_period` sets `prior_start` and
`prior_end` to `NULL` outright — there is no window before all time. The DAX
has no such concept, so it computes one: 801 days before the first day of data,
`2022-06-10` to `2024-08-18`. The guard then rejects it, because that start
precedes the first sale, and the measure returns `BLANK` — the same answer the
SQL gives, by a different route.

## Verified, not assumed

The equivalences above were checked numerically against the synthetic
warehouse by reimplementing each measure's DAX semantics in SQL and diffing
against the model:

| Check | Result |
|---|---|
| Prior window bounds vs `dim_period.prior_start/end` | identical for all four real periods |
| `net_sales_cents`, `orders`, `avg_order_value_cents`, `units_per_order` | exact across all 5 periods |
| `prior_net_sales_cents`, `net_sales_change_pct` | exact, including the `NULL` case |
| 7-day rolling: `sum/7` vs `avg()` over 7 rows | 801/801 days, max difference 0 |

That is a check of the arithmetic, not of the model. It does not prove the
relationships are drawn correctly, the storage mode behaves, or a visual
filters the way it appears to — which is what `compare_dax_to_dbt.py` is for,
reading the published semantic model rather than a transcription of it.

## Scope

`fact_order_line_margin` has no `sale_date` and no `location_key`; it inherits
both from the order line through `order_line_key`. Relating it to `dim_date`
therefore needs either a fact-to-fact relationship or a calculated column, so
margin measures are deliberately absent here. The margin models are also empty
on production until real vendor costs exist — see
[`docs/powerbi.md`](../../docs/powerbi.md), which explains why synthetic costs
against real revenue would be worse than no margin at all.
