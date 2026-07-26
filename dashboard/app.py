"""RetailPulse KPI dashboard (Streamlit).

Reads the Gold DuckDB warehouse read-only and renders the tested dbt KPI
models. It runs no SQL business logic of its own — every number here comes
from a `kpi_*` model in dbt/models/marts/kpi/, so the dashboard and the
tested warehouse can never disagree.

The period control at the top is the spine of the whole page. It doesn't
recompute anything: `dim_period` defines each window, the KPI models are
built at (period, key) grain, and the dashboard just picks the matching
rows. Comparisons against the previous equivalent window come from the
models too — including the decision to hide a comparison when the extract
doesn't cover the earlier window.

Search, category filters, sorting and top-N are presentation only: they
choose which model rows to show, they never compute a new figure.

Everything visual — palette, type scale, chart theme, card treatment — comes
from `design.py` and `.streamlit/config.toml`. Charts pass `theme=None` so
the registered RetailPulse Altair theme wins over Streamlit's built-in one;
without that the charts would be styled to Streamlit's look while the chrome
follows ours.

Run with `make dashboard` (or `streamlit run dashboard/app.py`).
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

import design

WAREHOUSE_PATH = Path(
    os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
)
WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_PERIOD = "Last 30 days"

# One height scale for every chart, so nothing looks accidental.
CHART_TALL = 290
CHART_SHORT = 190
TILE_HEIGHT = 186
TILE_COMPACT = 116

# Item-table sort choices -> (column, ascending). Every column here is a
# published column of kpi_item_sales; sorting is presentation, not new logic.
SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    "Best sellers": ("units", False),
    "Most revenue": ("net_sales_cents", False),
    "Biggest gainers": ("units_change_pct", False),
    "Biggest drops": ("units_change_pct", True),
    "Most recently sold": ("last_sold_at", False),
    "Name (A–Z)": ("item_name", True),
}
ROW_LIMITS = [25, 50, 100, 250, 1000, "All"]
SPARKLINE_WEEKS = 12


@st.cache_data(ttl=60)
def query(sql: str) -> pd.DataFrame:
    # Open a short-lived read-only connection per query so the dashboard
    # never holds a lock on the warehouse file (dbt can rebuild freely).
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


# --- formatting ------------------------------------------------------------


def dollars(cents: float) -> str:
    return f"${cents / 100:,.2f}"


def units(value: float) -> str:
    return f"{value:,.0f}"


def change(pct: float) -> str | None:
    """Delta string for st.metric, or None when there's nothing to compare to.

    Returning None matters: the models deliberately publish a null rather
    than a 0 when a comparison isn't possible, and showing "0%" would put a
    number on screen that nothing in the warehouse supports.
    """
    if pd.isna(pct):
        return None
    return f"{pct:+.1f}%"


def item_label(row: pd.Series) -> str:
    """Human label for an item variation ('Item — Variation' when useful)."""
    name = row["item_name"] or "(unnamed)"
    variation = row.get("variation_name")
    if variation and variation != name and str(variation).lower() != "regular":
        return f"{name} — {variation}"
    return name


def table_height(rows: int, max_rows: int = 12) -> int:
    """Height that ends on a row boundary, so tables never clip mid-row."""
    return 35 * min(max(rows, 1), max_rows) + 40


def money_tip(field: str, title: str) -> alt.Tooltip:
    return alt.Tooltip(field, title=title, format="$,.2f")


def chart(figure: alt.Chart | alt.LayerChart) -> None:
    """Render with the RetailPulse Altair theme rather than Streamlit's."""
    st.altair_chart(figure, width="stretch", theme=None)


def bar_width(n: int, span: int = 900) -> float:
    """Bar thickness: fill the band but never exceed the 24px mark cap.

    Leaving the leftover band as air is what produces the surface gap between
    neighbouring bars — the separation comes from the gap, not from a stroke
    drawn around each bar.
    """
    return min(design.BAR_MAX_WIDTH, max(2.0, (span / max(n, 1)) * 0.62))


# --- data access (thin wrappers over the KPI models) -----------------------
#
# Each loader pulls every period at once and the page filters to the selected
# one in pandas. The tables are small (the largest is ~14k rows), and it keeps
# the SQL here to plain selects with no predicates of its own.


def load_periods() -> pd.DataFrame:
    return query(
        "select period_label, period_days, period_order, period_start, period_end, "
        "prior_start, prior_end, prior_window_complete, as_of_date "
        "from main_marts.dim_period order by period_order"
    )


def load_coverage() -> pd.Series:
    return query("select * from main_marts.kpi_data_coverage").iloc[0]


def load_summary() -> pd.DataFrame:
    return query("select * from main_marts.kpi_summary order by period_order")


def load_daily() -> pd.DataFrame:
    return query(
        "select sale_date, day_name, orders, units, net_sales_cents, avg_order_value_cents, "
        "net_sales_7d_avg_cents from main_marts.kpi_daily_sales order by sale_date"
    )


def load_categories() -> pd.DataFrame:
    return query(
        "select period_label, category_name, orders, units, net_sales_cents, pct_of_net_sales, "
        "prior_net_sales_cents, net_sales_change_pct from main_marts.kpi_sales_by_category "
        "order by period_order, net_sales_cents desc"
    )


def load_weekday() -> pd.DataFrame:
    return query(
        "select period_label, day_of_week, day_name, orders, net_sales_cents "
        "from main_marts.kpi_sales_by_weekday order by period_order, day_of_week"
    )


def load_hourly() -> pd.DataFrame:
    return query(
        "select period_label, hour_of_day, orders, net_sales_cents "
        "from main_marts.kpi_sales_by_hour order by period_order, hour_of_day"
    )


def load_payments() -> pd.DataFrame:
    return query(
        "select period_label, source_type, payments, amount_collected_cents, "
        "processing_fee_cents, pct_of_collected from main_marts.kpi_payment_methods "
        "order by period_order, amount_collected_cents desc"
    )


def load_items() -> pd.DataFrame:
    return query(
        "select period_label, variation_id, item_name, variation_name, category_name, "
        "units, orders, net_sales_cents, avg_weekly_units, prior_units, "
        "prior_net_sales_cents, units_change_pct, net_sales_change_pct, units_all_time, "
        "first_sold_at, last_sold_at from main_marts.kpi_item_sales "
        "order by period_order, units desc"
    )


def load_weekly() -> pd.DataFrame:
    return query(
        "select week_start, variation_id, item_name, variation_name, orders, units_sold, "
        "net_sales_cents from main_marts.kpi_item_weekly_sales order by week_start"
    )


def load_forecast() -> pd.DataFrame:
    return query(
        "select variation_id, item_name, forecast_week_start, weeks_ahead, weeks_of_history, "
        "forecast_units, method from main_marts.kpi_item_forecast"
    )


def sparklines(weekly: pd.DataFrame, weeks: int = SPARKLINE_WEEKS) -> dict[str, list[float]]:
    """Recent weekly-unit series per item, for the in-table trend column.

    Weeks with no sales become explicit zeros so every sparkline covers the
    same span and they can be read against each other.
    """
    if weekly.empty:
        return {}
    recent = sorted(weekly["week_start"].unique())[-weeks:]
    window = weekly[weekly["week_start"].isin(recent)]
    pivot = (
        window.pivot_table(
            index="variation_id", columns="week_start", values="units_sold", aggfunc="sum"
        )
        .reindex(columns=recent)
        .fillna(0)
    )
    return {vid: [float(v) for v in row] for vid, row in zip(pivot.index, pivot.to_numpy())}


def for_period(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    return frame[frame["period_label"] == period]


# --- header & period control -----------------------------------------------


def render_header(coverage: pd.Series, p: design.Palette) -> None:
    stale = int(coverage["days_since_last_sale"])
    if stale == 0:
        tone, label = "good", "Up to date"
    elif stale == 1:
        tone, label = "good", "Through yesterday"
    else:
        tone, label = "warning", f"{stale} days behind"

    subtitle = (
        f"{coverage['first_sale_date']:%b %-d, %Y} – {coverage['last_sale_date']:%b %-d, %Y}"
        f" · {int(coverage['days_covered']):,} days"
        f" · {int(coverage['orders']):,} sales"
        f" · {int(coverage['items_sold']):,} items"
    )
    design.header("RetailPulse", subtitle, label, tone, p)


def render_period_picker(periods: pd.DataFrame) -> pd.Series:
    """The control that drives the entire page. Returns the chosen period row."""
    labels = periods["period_label"].tolist()
    default = DEFAULT_PERIOD if DEFAULT_PERIOD in labels else labels[0]
    chosen = st.segmented_control(
        "Period",
        options=labels,
        default=default,
        key="period",
        label_visibility="collapsed",
        help="Changes every number on the page. Each window is compared against "
        "the same length of time immediately before it.",
    )
    # segmented_control returns None if the user clicks the active pill off.
    period = periods[periods["period_label"] == (chosen or default)].iloc[0]

    caption = (
        f"**{period['period_start']:%b %-d} – {period['period_end']:%b %-d, %Y}**"
        if period["period_days"]
        else f"**Everything through {period['period_end']:%b %-d, %Y}**"
    )
    if period["prior_window_complete"]:
        caption += (
            f" · compared with the previous {int(period['period_days'])} days "
            f"({period['prior_start']:%b %-d} – {period['prior_end']:%b %-d})"
        )
    else:
        caption += " · no comparison — not enough history behind this window yet"
    st.caption(caption)
    return period


# --- tab: overview ---------------------------------------------------------


def render_headline(summary: pd.Series, daily: pd.DataFrame, period: pd.Series) -> None:
    # Row 1 is the four measures kpi_daily_sales also publishes a daily series
    # for, so every tile in it carries a spark line and the row stays uniform.
    # Ratios and totals without a daily series live in row 2.
    window = daily[
        (daily["sale_date"] >= pd.Timestamp(period["period_start"]))
        & (daily["sale_date"] <= pd.Timestamp(period["period_end"]))
    ]
    net_series = window["net_sales_cents"].div(100).tolist()
    order_series = window["orders"].tolist()
    aov_series = window["avg_order_value_cents"].div(100).tolist()
    unit_series = window["units"].tolist()

    vs = (
        f"vs. the previous {int(period['period_days'])} days"
        if period["prior_window_complete"]
        else "no comparable earlier window in the data yet"
    )

    row1 = st.columns(4)
    row1[0].metric(
        "Sales",
        dollars(summary["net_sales_cents"]),
        delta=change(summary["net_sales_change_pct"]),
        border=True,
        height=TILE_HEIGHT,
        chart_data=net_series,
        chart_type="area",
        help=f"After discounts, before tax. Change is {vs}.",
    )
    row1[1].metric(
        "Sales count",
        f"{int(summary['orders']):,}",
        delta=change(summary["orders_change_pct"]),
        border=True,
        height=TILE_HEIGHT,
        chart_data=order_series,
        chart_type="area",
        help=f"Number of transactions rung up. Change is {vs}.",
    )
    row1[2].metric(
        "Average sale",
        dollars(summary["avg_order_value_cents"]),
        border=True,
        height=TILE_HEIGHT,
        chart_data=aov_series,
        chart_type="area",
        help="Sales divided by transactions.",
    )
    row1[3].metric(
        "Items sold",
        f"{int(summary['units']):,}",
        delta=change(summary["units_change_pct"]),
        border=True,
        height=TILE_HEIGHT,
        chart_data=unit_series,
        chart_type="area",
        help=f"{summary['units_per_order']:.2f} items per transaction on average. Change is {vs}.",
    )

    # Four to a row throughout: five tiles at this width truncates the
    # currency, and dropping the cents to fit would clash with row 1.
    row2 = st.columns(4)
    row2[0].metric("Discounts given", dollars(summary["discount_cents"]), border=True)
    row2[1].metric("Sales tax", dollars(summary["tax_cents"]), border=True)
    row2[2].metric(
        "Card fees",
        dollars(summary["processing_fee_cents"]),
        border=True,
        help="What Square charged to process card payments in this period.",
    )
    row2[3].metric(
        "Money collected",
        dollars(summary["collected_cents"]),
        border=True,
        help="What actually changed hands: sales + tax (+ tips).",
    )

    # Margin tiles only when vendor costs are on file. DB nulls arrive as
    # pandas NaN (a float), so `is not None` isn't enough — use pd.notna so a
    # store with no costs simply doesn't see these tiles.
    gross_profit = summary["gross_profit_cents"]
    margin_pct = summary["gross_margin_pct"]
    coverage_pct = summary["cost_coverage_pct"]
    if pd.notna(gross_profit):
        row3 = st.columns(4)
        row3[0].metric("Cost of goods", dollars(summary["cogs_cents"]), border=True)
        row3[1].metric("Gross profit", dollars(gross_profit), border=True)
        row3[2].metric(
            "Gross margin",
            f"{margin_pct:.1f}%" if pd.notna(margin_pct) else "—",
            border=True,
        )
        row3[3].metric(
            "Cost coverage",
            f"{coverage_pct:.0f}%" if pd.notna(coverage_pct) else "—",
            border=True,
            help="Share of sales for which a vendor cost is on file. "
            "Profit is worked out only over those lines.",
        )


def render_daily_sales(daily: pd.DataFrame, period: pd.Series, p: design.Palette) -> None:
    with st.container(border=True):
        st.subheader(
            "Sales by day",
            help="Each day's sales with a 7-day rolling average over them. The average "
            "smooths out the weekday pattern and needs a full week of history before it "
            "starts.",
        )
        window = daily[
            (daily["sale_date"] >= pd.Timestamp(period["period_start"]))
            & (daily["sale_date"] <= pd.Timestamp(period["period_end"]))
        ]
        if window.empty:
            st.info("No sales in this period.")
            return

        window = window.assign(
            net_sales=window["net_sales_cents"] / 100,
            avg_7d=window["net_sales_7d_avg_cents"] / 100,
            daily_series="Daily sales",
            avg_series="7-day average",
        )

        # Short windows need ticks pinned to whole days — left to itself Vega
        # subdivides a week into hours and labels them "12 PM". Long windows
        # need the opposite: daily ticks over a year get thinned to bare
        # day-of-month labels that repeat across months, so there Vega picks
        # the interval and the format names the month.
        if len(window) <= 45:
            x_axis = alt.Axis(tickCount={"interval": "day", "step": 1}, labelOverlap=True)
        else:
            x_axis = alt.Axis(format="%b %-d", labelOverlap=True)
        x = alt.X("sale_date:T", title=None, axis=x_axis)
        y = alt.Y("net_sales:Q", title=None, axis=alt.Axis(format="$,.0f"))

        # Two series share one axis (both are dollars), so they belong on one
        # plot — and because there are two, a legend is required. Encoding the
        # series name as colour is what produces it; hard-coded mark colours
        # would leave identity to the caption alone.
        scale = alt.Scale(
            domain=["Daily sales", "7-day average"], range=[p.series_1, p.series_2]
        )
        legend = alt.Legend(title=None)
        tooltip = [
            alt.Tooltip("sale_date:T", title="Date", format="%b %-d, %Y"),
            alt.Tooltip("day_name:N", title="Day"),
            money_tip("net_sales:Q", "Sales"),
            money_tip("avg_7d:Q", "7-day average"),
            alt.Tooltip("orders:Q", title="Transactions", format=","),
            alt.Tooltip("units:Q", title="Items", format=","),
        ]

        base = alt.Chart(window)
        if len(window) > 120:
            # Past ~120 days the bars are thinner than the gap between them and
            # read as a comb; a wash of the same hue carries the shape better.
            daily_mark = base.mark_area(opacity=design.AREA_OPACITY, line=False).encode(
                x=x, y=y, color=alt.Color("daily_series:N", scale=scale, legend=legend),
                tooltip=tooltip,
            )
        else:
            daily_mark = base.mark_bar(
                size=bar_width(len(window)), cornerRadiusEnd=design.CORNER_RADIUS
            ).encode(
                x=x, y=y, color=alt.Color("daily_series:N", scale=scale, legend=legend),
                tooltip=tooltip,
            )
        avg_line = base.mark_line(
            strokeWidth=design.LINE_WIDTH, strokeJoin="round", strokeCap="round"
        ).encode(
            x=x,
            y=alt.Y("avg_7d:Q"),
            color=alt.Color("avg_series:N", scale=scale, legend=legend),
            tooltip=tooltip,
        )
        chart((daily_mark + avg_line).properties(height=CHART_TALL))


def render_categories(categories: pd.DataFrame) -> None:
    # "stretch" makes the two overview cards share the taller one's height,
    # so the row reads as one band rather than two mismatched panels.
    with st.container(border=True, height="stretch"):
        st.subheader(
            "What sells most",
            help="Sales by category for this period. Categories are tidied up "
            "(case, spacing, and your override list) so duplicates from the Square "
            "catalog are already merged.",
        )
        if categories.empty:
            st.info("No sales in this period.")
            return
        shown = categories.head(12).assign(net_sales=lambda d: d["net_sales_cents"] / 100)
        # One series, one colour: shading each bar by its own value would
        # double-encode the length as hue and burn the free channel.
        cat_chart = (
            alt.Chart(shown)
            .mark_bar(cornerRadiusEnd=design.CORNER_RADIUS, height=design.BAR_MAX_WIDTH - 6)
            .encode(
                # The theme puts gridlines on Y because most charts here are
                # vertical. Bars laid out horizontally invert that: the value
                # axis is X, so the grid moves with it and the category axis
                # goes bare.
                x=alt.X("net_sales:Q", title=None, axis=alt.Axis(format="$,.0f", grid=True)),
                # Never drop a bar's label: Vega hides alternates by default at
                # this row count, which reads as a chart with missing rows.
                y=alt.Y(
                    "category_name:N",
                    sort="-x",
                    title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=170, grid=False),
                ),
                tooltip=[
                    alt.Tooltip("category_name:N", title="Category"),
                    money_tip("net_sales:Q", "Sales"),
                    alt.Tooltip("pct_of_net_sales:Q", title="Share of sales", format=".1f"),
                    alt.Tooltip("units:Q", title="Items", format=","),
                ],
            )
            .properties(height=max(CHART_SHORT, 26 * len(shown)))
        )
        chart(cat_chart)
        if len(categories) > len(shown):
            with st.expander(f"All {len(categories)} categories"):
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Category": categories["category_name"],
                            "Transactions": categories["orders"],
                            "Items": categories["units"],
                            "Sales": categories["net_sales_cents"] / 100,
                            "Share": categories["pct_of_net_sales"],
                            "vs. previous": categories["net_sales_change_pct"],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                    height=table_height(len(categories)),
                    column_config={
                        "Transactions": st.column_config.NumberColumn(format="localized"),
                        "Items": st.column_config.NumberColumn(format="localized"),
                        "Sales": st.column_config.NumberColumn(format="dollar"),
                        "Share": st.column_config.NumberColumn(format="%.2f%%"),
                        "vs. previous": st.column_config.NumberColumn(format="%+.1f%%"),
                    },
                )


def render_when_you_sell(weekday: pd.DataFrame, hourly: pd.DataFrame) -> None:
    with st.container(border=True, height="stretch"):
        st.subheader(
            "When you sell",
            help="Times come from Square in UTC, not store-local time — a late-evening "
            "sale can land on the next day here.",
        )
        if weekday.empty:
            st.info("No sales in this period.")
            return
        wd = weekday.assign(net_sales=weekday["net_sales_cents"] / 100)
        wd_chart = (
            alt.Chart(wd)
            .mark_bar(size=design.BAR_MAX_WIDTH, cornerRadiusEnd=design.CORNER_RADIUS)
            .encode(
                x=alt.X(
                    "day_name:N",
                    sort=WEEKDAY_ORDER,
                    title=None,
                    # Abbreviate in the axis rather than the data, so all seven
                    # days stay labelled at any card width.
                    axis=alt.Axis(labelAngle=0, labelExpr="slice(datum.label, 0, 3)"),
                ),
                y=alt.Y("net_sales:Q", title=None, axis=alt.Axis(format="$,.0f")),
                tooltip=[
                    alt.Tooltip("day_name:N", title="Day"),
                    money_tip("net_sales:Q", "Sales"),
                    alt.Tooltip("orders:Q", title="Transactions", format=","),
                ],
            )
            .properties(height=CHART_SHORT)
        )
        chart(wd_chart)

        hr = hourly.assign(net_sales=hourly["net_sales_cents"] / 100)
        hour_chart = (
            alt.Chart(hr)
            .mark_bar(size=bar_width(len(hr), span=560), cornerRadiusEnd=design.CORNER_RADIUS)
            .encode(
                x=alt.X("hour_of_day:O", title="Hour of day (UTC)", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("net_sales:Q", title=None, axis=alt.Axis(format="$,.0f")),
                tooltip=[
                    alt.Tooltip("hour_of_day:O", title="Hour (UTC)"),
                    money_tip("net_sales:Q", "Sales"),
                    alt.Tooltip("orders:Q", title="Transactions", format=","),
                ],
            )
            .properties(height=CHART_SHORT)
        )
        chart(hour_chart)


def render_overview(
    summary: pd.Series,
    daily: pd.DataFrame,
    categories: pd.DataFrame,
    weekday: pd.DataFrame,
    hourly: pd.DataFrame,
    period: pd.Series,
    p: design.Palette,
) -> None:
    render_headline(summary, daily, period)
    render_daily_sales(daily, period, p)
    left, right = st.columns(2)
    with left:
        render_categories(categories)
    with right:
        render_when_you_sell(weekday, hourly)


# --- tab: items ------------------------------------------------------------


def item_filters(items: pd.DataFrame) -> pd.DataFrame:
    """Search/category/sort/top-N controls. Returns the rows to display."""
    with st.container(border=True):
        c1, c2 = st.columns([2, 2])
        search = c1.text_input(
            "Search", placeholder="item, variation or category…", key="item_search"
        )
        categories = sorted(c for c in items["category_name"].dropna().unique())
        chosen = c2.multiselect("Categories", options=categories, key="item_categories")

        c3, c4, c5 = st.columns([2, 1, 1])
        sort_label = c3.selectbox("Sort by", list(SORT_OPTIONS), key="item_sort")
        limit = c4.selectbox("Show", ROW_LIMITS, index=1, key="item_limit")
        min_units = c5.number_input(
            "Min items sold",
            min_value=0,
            value=0,
            step=1,
            key="item_min_units",
            help="Hides the long tail. Worth raising when sorting by gainers or drops: "
            "an item that went from 1 to 3 shows as +200%.",
        )

    view = items
    if search:
        needle = search.strip().lower()
        haystack = (
            view["item_name"].fillna("")
            + " "
            + view["variation_name"].fillna("")
            + " "
            + view["category_name"].fillna("")
        )
        view = view[haystack.str.lower().str.contains(needle, regex=False)]
    if chosen:
        view = view[view["category_name"].isin(chosen)]
    if min_units:
        view = view[view["units"] >= min_units]

    column, ascending = SORT_OPTIONS[sort_label]
    view = view.sort_values(column, ascending=ascending, na_position="last")
    if limit != "All":
        view = view.head(int(limit))
    return view


def render_item_table(
    view: pd.DataFrame, total_items: int, spark: dict[str, list[float]], period: pd.Series
) -> None:
    if view.empty:
        st.info("No items match these filters. Try clearing the search or category picker.")
        return

    display = pd.DataFrame(
        {
            "Item": view.apply(item_label, axis=1),
            "Category": view["category_name"].fillna("—"),
            "Trend": view["variation_id"].map(lambda v: spark.get(v, [])),
            "Sold": view["units"],
            "vs. prev": view["units_change_pct"],
            "Per week": view["avg_weekly_units"],
            "Sales": view["net_sales_cents"] / 100,
            "Last sold": pd.to_datetime(view["last_sold_at"]),
        }
    )
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        height=table_height(len(display)),
        column_config={
            "Item": st.column_config.TextColumn(width="large", pinned=True),
            "Category": st.column_config.TextColumn(width="medium"),
            "Trend": st.column_config.LineChartColumn(
                f"{SPARKLINE_WEEKS}-wk trend",
                y_min=0,
                help=f"Weekly sales over the last {SPARKLINE_WEEKS} weeks. This one always "
                "shows the same span, whatever period is selected.",
            ),
            "Sold": st.column_config.NumberColumn(
                format="localized", help="Items sold in the selected period."
            ),
            "vs. prev": st.column_config.NumberColumn(
                format="%+.0f%%",
                # A low-volume item can swing +2600%; without a fixed width the
                # column sizes to the short header and clips those values.
                width="small",
                help="Change against the same length of time immediately before this period.",
            ),
            "Per week": st.column_config.NumberColumn(
                format="%.1f", help="Average items sold per week inside this period."
            ),
            "Sales": st.column_config.NumberColumn(format="dollar"),
            "Last sold": st.column_config.DateColumn(format="MMM D, YYYY"),
        },
    )
    foot, download = st.columns([3, 1], vertical_alignment="center")
    foot.caption(
        f"Showing {len(view):,} of {total_items:,} items that sold in this period. "
        "Every column is a real number — click a header to sort."
    )
    download.download_button(
        "Download CSV",
        data=view.to_csv(index=False).encode("utf-8"),
        file_name=f"retailpulse_items_{str(period['period_label']).replace(' ', '_').lower()}.csv",
        mime="text/csv",
        width="stretch",
        help="Saves locally. This is your real store data — don't share it publicly.",
    )


def render_item_detail(
    view: pd.DataFrame, weekly: pd.DataFrame, forecast: pd.DataFrame, p: design.Palette
) -> None:
    with st.container(border=True):
        st.subheader(
            "Item detail",
            help="Weekly history for one item with its next-4-week projection. The picker "
            "is limited to the items matching the filters above. The chart always shows "
            "full history, not just the selected period.",
        )
        if view.empty:
            st.info("Pick filters that match at least one item to see its history.")
            return

        labels = {row["variation_id"]: item_label(row) for _, row in view.iterrows()}
        chosen_id = st.selectbox(
            "Item",
            options=list(labels),
            format_func=lambda vid: labels[vid],
            key="item_detail_pick",
            label_visibility="collapsed",
        )
        row = view[view["variation_id"] == chosen_id].iloc[0]

        item_fc = forecast[forecast["variation_id"] == chosen_id].sort_values("weeks_ahead")
        next_week = item_fc[item_fc["weeks_ahead"] == 1]

        # An explicit height keeps the one tile carrying a delta from making
        # the row ragged. The Sales column is weighted wider than the rest
        # because it's the only one holding money to the cent — over a year
        # that's "$144,661.50", which an equal fifth truncates.
        tiles = st.columns([1, 1, 1, 1.35, 1])
        tiles[0].metric(
            "Sold",
            units(row["units"]),
            delta=change(row["units_change_pct"]),
            border=True,
            height=TILE_COMPACT,
            help="Items sold in the selected period, and the change against the "
            "equivalent stretch before it.",
        )
        tiles[1].metric(
            "Per week",
            f"{row['avg_weekly_units']:.1f}" if pd.notna(row["avg_weekly_units"]) else "—",
            border=True,
            height=TILE_COMPACT,
        )
        tiles[2].metric(
            "Sold all time", units(row["units_all_time"]), border=True, height=TILE_COMPACT
        )
        tiles[3].metric(
            "Sales", dollars(row["net_sales_cents"]), border=True, height=TILE_COMPACT
        )
        tiles[4].metric(
            # Kept short: the uppercase, letter-spaced card labels are wide,
            # and anything longer truncates in a fifth-width tile.
            "Next week",
            units(next_week["forecast_units"].iloc[0]) if not next_week.empty else "—",
            border=True,
            height=TILE_COMPACT,
            help="Estimate, projected from recent weekly sales — see the Forecast tab.",
        )

        history = weekly[weekly["variation_id"] == chosen_id].sort_values("week_start")
        if history.empty:
            st.info("No weekly history for this item yet.")
            return

        actual = pd.DataFrame(
            {
                "week": pd.to_datetime(history["week_start"]),
                "units": history["units_sold"].astype(float),
                "series": "Actual",
            }
        )
        frames = [actual]
        if not item_fc.empty:
            projected = pd.DataFrame(
                {
                    "week": pd.to_datetime(item_fc["forecast_week_start"]),
                    "units": item_fc["forecast_units"].astype(float),
                    "series": "Projected",
                }
            )
            # Repeat the last actual point as the projection's first point so
            # the two lines meet instead of floating apart.
            bridge = actual.tail(1).assign(series="Projected")
            frames.append(pd.concat([bridge, projected], ignore_index=True))
        chart_data = pd.concat(frames, ignore_index=True)

        # Two series: colour carries identity and the legend names it, with the
        # dash pattern as a second channel so the split survives CVD and print.
        domain = ["Actual", "Projected"]
        color = alt.Color(
            "series:N",
            title=None,
            scale=alt.Scale(domain=domain, range=[p.series_1, p.series_2]),
            legend=alt.Legend(title=None),
        )
        dash = alt.StrokeDash(
            "series:N",
            title=None,
            scale=alt.Scale(domain=domain, range=[[1, 0], [5, 4]]),
            legend=alt.Legend(title=None),
        )
        encoding = {
            "x": alt.X("week:T", title=None),
            "y": alt.Y("units:Q", title="Items sold per week"),
            "color": color,
            "tooltip": [
                alt.Tooltip("week:T", title="Week of", format="%b %-d, %Y"),
                alt.Tooltip("units:Q", title="Items", format=","),
                alt.Tooltip("series:N", title="Series"),
            ],
        }
        base = alt.Chart(chart_data)
        line = base.mark_line(
            strokeWidth=design.LINE_WIDTH, strokeJoin="round", strokeCap="round"
        ).encode(strokeDash=dash, **encoding)
        # A 2px ring in the surface colour keeps markers legible where the two
        # series overlap, and enlarges the hover target.
        points = base.mark_point(
            size=design.POINT_SIZE, filled=True, stroke=p.surface, strokeWidth=2
        ).encode(**encoding)
        chart((line + points).properties(height=280))
        st.caption(
            "Solid: what actually sold each week. Dashed: the next 4 weeks projected. "
            "The first and last weeks shown can be partial, so they may sit below trend."
        )


def render_items(
    items: pd.DataFrame,
    weekly: pd.DataFrame,
    forecast: pd.DataFrame,
    period: pd.Series,
    p: design.Palette,
) -> None:
    st.subheader(
        "Item sales",
        help="How much of each item sold in the selected period, how that compares with "
        "the previous equivalent stretch, and what it averages per week.",
    )
    if items.empty:
        st.info("Nothing sold in this period.")
        return

    view = item_filters(items)
    render_item_table(view, len(items), sparklines(weekly), period)
    render_item_detail(view, weekly, forecast, p)


# --- tab: forecast ---------------------------------------------------------


def render_forecast(all_time_items: pd.DataFrame, forecast: pd.DataFrame) -> None:
    st.subheader(
        "What to expect — next 4 weeks",
        help="A trend line fitted per item over its last few complete weeks, on a series "
        "that counts quiet weeks as zero. Estimates only: holidays and weather aren't "
        "modelled. This tab always uses full history, so it doesn't follow the period "
        "control above.",
    )
    if forecast.empty:
        st.info(
            "Not enough weekly sales history to project yet — needs at least a couple of "
            "complete weeks."
        )
        return

    totals = (
        forecast.groupby(["forecast_week_start", "weeks_ahead"], as_index=False)["forecast_units"]
        .sum()
        .sort_values("weeks_ahead")
    )
    tcols = st.columns(len(totals))
    for i, (_, r) in enumerate(totals.iterrows()):
        weeks_ahead = int(r["weeks_ahead"])
        label = "Next week" if weeks_ahead == 1 else f"In {weeks_ahead} weeks"
        tcols[i].metric(
            label,
            f"{int(r['forecast_units']):,}",
            border=True,
            help=f"Items expected to sell in the week starting "
            f"{r['forecast_week_start']:%b %-d, %Y}",
        )

    fallbacks = int(forecast.loc[forecast["method"] == "avg_fallback", "variation_id"].nunique())
    median_history = int(forecast["weeks_of_history"].median())
    st.caption(
        f"Projected items across the whole catalog, from a median of {median_history} weeks "
        "of history per item"
        + (
            f"; {fallbacks:,} item(s) had too little history for a trend and fall back to "
            "their average."
            if fallbacks
            else " — every item had enough history for a trend fit."
        )
    )

    with st.container(border=True):
        head, control = st.columns([3, 1], vertical_alignment="bottom")
        with head:
            st.subheader("Expected sales by item and week")
        search = control.text_input(
            "Search", placeholder="item name…", key="forecast_search", label_visibility="collapsed"
        )

        pivot = forecast.pivot_table(
            index="variation_id",
            columns="forecast_week_start",
            values="forecast_units",
            aggfunc="sum",
        ).fillna(0)
        pivot.columns = [f"Wk of {pd.Timestamp(c):%b %-d}" for c in pivot.columns]
        week_columns = list(pivot.columns)

        names = all_time_items.set_index("variation_id")
        table = pivot.join(names[["item_name", "variation_name", "category_name"]], how="left")
        table["Item"] = table.apply(item_label, axis=1)
        table["Category"] = table["category_name"].fillna("—")
        table["Next 4 weeks"] = table[week_columns].sum(axis=1)

        if search:
            needle = search.strip().lower()
            table = table[table["Item"].str.lower().str.contains(needle, regex=False)]
        table = table.sort_values("Next 4 weeks", ascending=False)
        shown = table.head(100)

        if shown.empty:
            st.info("No items match that search.")
            return
        st.dataframe(
            shown[["Item", "Category", *week_columns, "Next 4 weeks"]],
            hide_index=True,
            width="stretch",
            height=table_height(len(shown)),
            column_config={
                "Item": st.column_config.TextColumn(width="large", pinned=True),
                "Category": st.column_config.TextColumn(width="small"),
                **{
                    col: st.column_config.NumberColumn(format="localized")
                    for col in [*week_columns, "Next 4 weeks"]
                },
            },
        )
        st.caption(f"Showing {len(shown):,} of {len(table):,} items, busiest first.")


# --- tab: back office ------------------------------------------------------


def render_reconciliation() -> None:
    with st.container(border=True):
        st.subheader(
            "Do the books balance?",
            help="Checks that what the orders say was sold matches what the payments say "
            "was collected. Orders paid over are tips and are expected. This check covers "
            "the whole extract, not just the selected period.",
        )
        recon = query(
            "select reconciliation_status, count(*) as n "
            "from main_marts.rpt_order_payment_reconciliation group by 1"
        )
        sc = dict(zip(recon["reconciliation_status"], recon["n"].astype(int)))
        matched = sc.get("matched", 0)
        overpaid = sc.get("overpaid", 0)  # tips: collected > recorded sales+tax
        short = sc.get("short", 0)
        pay_no_order = sc.get("payment_without_order", 0)
        tie = matched + overpaid
        total_orders = tie + short + sc.get("order_without_payment", 0)
        if total_orders:
            pct = 100.0 * tie / total_orders
            st.success(
                f"{tie:,} of {total_orders:,} sales ({pct:.1f}%) match their payments — "
                f"{matched:,} to the cent, {overpaid:,} paid over (tips).",
                icon="✅",
            )
        notes = []
        if short:
            notes.append(
                f"{short:,} sale(s) collected **less** than recorded "
                "(refunds, comps or unpaid balances)"
            )
        if pay_no_order:
            notes.append(
                f"{pay_no_order:,} payment(s) have no matching sale in this pull "
                "(they belong to orders from before the extract window)"
            )
        if notes:
            st.caption("Notes: " + "; ".join(notes) + ".")


def render_payments(payments: pd.DataFrame) -> None:
    with st.container(border=True):
        st.subheader("How customers pay", help="Tender mix for the selected period.")
        if payments.empty:
            st.info("No payments in this period.")
            return
        st.dataframe(
            pd.DataFrame(
                {
                    "Method": payments["source_type"],
                    "Payments": payments["payments"],
                    "Collected": payments["amount_collected_cents"] / 100,
                    "Fees": payments["processing_fee_cents"] / 100,
                    "Share": payments["pct_of_collected"],
                }
            ),
            hide_index=True,
            width="stretch",
            height=table_height(len(payments)),
            column_config={
                "Payments": st.column_config.NumberColumn(format="localized"),
                "Collected": st.column_config.NumberColumn(format="dollar"),
                "Fees": st.column_config.NumberColumn(format="dollar"),
                "Share": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )


def render_margin() -> None:
    """Gross margin — only rendered when vendor costs are on file."""
    margin = query(
        "select category_name, net_sales_cents, gross_profit_cents, gross_margin_pct, "
        "line_cost_coverage_pct from main_marts.kpi_margin_by_category "
        "where gross_profit_cents is not null order by gross_profit_cents desc"
    )
    if margin.empty:
        return
    with st.container(border=True):
        st.subheader(
            "Profit by category",
            help="Gross profit = sales − what the goods cost you, using the vendor costs "
            "you maintain. Only lines with a known cost are included; this is true profit, "
            "not revenue.",
        )
        mleft, mright = st.columns([3, 2])
        with mleft:
            margin = margin.assign(gross_profit=margin["gross_profit_cents"] / 100)
            margin_chart = (
                alt.Chart(margin)
                .mark_bar(
                    cornerRadiusEnd=design.CORNER_RADIUS, height=design.BAR_MAX_WIDTH - 6
                )
                .encode(
                    # Horizontal bars: grid follows the value axis. See the
                    # same inversion in render_categories.
                    x=alt.X(
                        "gross_profit:Q", title=None, axis=alt.Axis(format="$,.0f", grid=True)
                    ),
                    y=alt.Y(
                        "category_name:N",
                        sort="-x",
                        title=None,
                        axis=alt.Axis(labelOverlap=False, labelLimit=170, grid=False),
                    ),
                    tooltip=[
                        alt.Tooltip("category_name:N", title="Category"),
                        money_tip("gross_profit:Q", "Gross profit"),
                        alt.Tooltip("gross_margin_pct:Q", title="Margin %", format=".1f"),
                    ],
                )
                .properties(height=max(CHART_SHORT, 26 * len(margin)))
            )
            chart(margin_chart)
        with mright:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Category": margin["category_name"],
                        "Sales": margin["net_sales_cents"] / 100,
                        "Gross profit": margin["gross_profit_cents"] / 100,
                        "Margin": margin["gross_margin_pct"],
                    }
                ),
                hide_index=True,
                width="stretch",
                height=table_height(len(margin)),
                column_config={
                    "Sales": st.column_config.NumberColumn(format="$%.0f"),
                    "Gross profit": st.column_config.NumberColumn(format="$%.0f"),
                    "Margin": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )


def render_back_office(payments: pd.DataFrame) -> None:
    render_reconciliation()
    render_payments(payments)
    render_margin()


# --- sidebar ---------------------------------------------------------------


def render_sidebar(coverage: pd.Series) -> None:
    with st.sidebar:
        st.subheader("Source")
        st.caption(f"`{WAREHOUSE_PATH}`")
        st.caption(
            f"Rebuilt through {coverage['last_sale_date']:%b %-d, %Y}. Read-only — the "
            "dashboard never writes to the warehouse."
        )
        if st.button("↻ Reload data", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        st.caption(
            "Built from your own Square data. Keep these figures local; the committed "
            "demo uses synthetic data."
        )


# --- app -------------------------------------------------------------------


def main() -> None:
    p = design.apply()

    if not WAREHOUSE_PATH.exists():
        st.error(
            f"Warehouse not found at `{WAREHOUSE_PATH}`.\n\n"
            "Build it first:\n\n"
            "```\nmake demo-data        # synthetic, no Square credentials needed\n"
            "# or, against your own store:\n"
            "make extract-sandbox\nmake silver\nmake dbt-build\n```"
        )
        return

    with st.spinner("Loading…"):
        periods = load_periods()
        coverage = load_coverage()
        summaries = load_summary()
        daily = load_daily()
        categories = load_categories()
        weekday = load_weekday()
        hourly = load_hourly()
        payments = load_payments()
        items = load_items()
        weekly = load_weekly()
        forecast = load_forecast()

    render_header(coverage, p)
    render_sidebar(coverage)
    period = render_period_picker(periods)
    label = period["period_label"]

    overview_tab, items_tab, forecast_tab, back_office_tab = st.tabs(
        ["Overview", "Items", "Forecast", "Back office"]
    )
    with overview_tab:
        render_overview(
            for_period(summaries, label).iloc[0],
            daily,
            for_period(categories, label),
            for_period(weekday, label),
            for_period(hourly, label),
            period,
            p,
        )
    with items_tab:
        render_items(for_period(items, label), weekly, forecast, period, p)
    with forecast_tab:
        # The forecast is fitted on full history and doesn't vary by period, so
        # names are resolved from the All time slice — anything narrower would
        # leave items that didn't sell recently without a label.
        render_forecast(for_period(items, "All time"), forecast)
    with back_office_tab:
        render_back_office(for_period(payments, label))


if __name__ == "__main__":
    main()
