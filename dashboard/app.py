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
from datetime import date
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
# Every KPI tile is now the compact height. The taller variant existed to hold
# a spark line under the value; those were removed because an axis-less,
# scale-less line can show a shape but cannot answer a question, and the same
# trend is in "Sales by day" below with axes and a 7-day average to read it
# against.
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

# Hour-of-day axis labels as a shopkeeper says them ("5p", not "17"). The Vega
# expression and the Python helper must agree — they label the same axis and
# its caption.
HOUR_LABEL = (
    "datum.value == 0 ? '12a' : datum.value < 12 ? datum.value + 'a'"
    " : datum.value == 12 ? '12p' : (datum.value - 12) + 'p'"
)


def _hour_label(hour: int) -> str:
    if hour == 0:
        return "12am"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "12pm"
    return f"{hour - 12}pm"


# Most date labels a time axis may show before ticks start being skipped.
MAX_DATE_LABELS = 14


@st.cache_data(ttl=60)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    # Open a short-lived read-only connection per query so the dashboard
    # never holds a lock on the warehouse file (dbt can rebuild freely).
    #
    # Window bounds are bound as parameters rather than formatted into the SQL.
    # They come from a date picker so they are already dates, but a query
    # string that is assembled by hand is a habit worth not having.
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql, params).df()
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


# --- data access (thin wrappers over the warehouse) ------------------------
#
# Everything windowed goes through the `rp_*_range` macros, which take the
# selected start and end dates. That is one code path for both the presets and
# an arbitrary range: a preset simply supplies the bounds `dim_period` already
# publishes for it, rather than reading a different set of tables.
#
# The reason that is safe — and the reason the presets did not lose their
# tested numbers when they started going through the macros — is the dbt test
# `assert_range_macros_match_periods`, which fails the build if a macro and its
# precomputed `kpi_*` model ever disagree on any of the five canonical windows.
#
# The dashboard still computes nothing. It chooses two dates.


def load_periods() -> pd.DataFrame:
    return query(
        "select period_label, period_days, period_order, period_start, period_end, "
        "prior_start, prior_end, prior_window_complete, as_of_date "
        "from main_marts.dim_period order by period_order"
    )


def load_coverage() -> pd.Series:
    return query("select * from main_marts.kpi_data_coverage").iloc[0]


def load_summary(start: date, end: date) -> pd.Series:
    return query("select * from rp_summary_range(?, ?)", (start, end)).iloc[0]


def load_daily() -> pd.DataFrame:
    return query(
        "select sale_date, day_name, orders, units, net_sales_cents, avg_order_value_cents, "
        "net_sales_7d_avg_cents from main_marts.kpi_daily_sales order by sale_date"
    )


def load_categories(start: date, end: date) -> pd.DataFrame:
    return query(
        "select category_name, orders, units, net_sales_cents, pct_of_net_sales, "
        "prior_net_sales_cents, net_sales_change_pct from rp_category_range(?, ?) "
        "order by net_sales_cents desc",
        (start, end),
    )


def load_weekday(start: date, end: date) -> pd.DataFrame:
    return query(
        "select day_of_week, day_name, orders, net_sales_cents "
        "from rp_weekday_range(?, ?) order by day_of_week",
        (start, end),
    )


def load_hourly(start: date, end: date) -> pd.DataFrame:
    return query(
        "select hour_of_day, orders, net_sales_cents "
        "from rp_hour_range(?, ?) order by hour_of_day",
        (start, end),
    )


def load_popular_times(start: date, end: date) -> pd.DataFrame:
    return query(
        "select day_of_week, day_name, hour_of_day, days_observed, orders, "
        "net_sales_cents, orders_per_day, busyness_pct from rp_popular_times_range(?, ?) "
        "order by day_of_week, hour_of_day",
        (start, end),
    )


def load_revenue_forecast() -> pd.DataFrame:
    return query(
        "select horizon_label, horizon_days, horizon_order, period_start, period_end, "
        "forecast_net_sales_cents, forecast_daily_avg_cents, trend_factor, method, "
        "wape_pct, baseline_wape_pct, mae_cents, backtest_days, points_better_than_baseline, "
        "as_of_date from main_marts.kpi_revenue_forecast order by horizon_order"
    )


def load_revenue_forecast_daily() -> pd.DataFrame:
    return query(
        "select forecast_date, days_ahead, day_name, forecast_net_sales_cents, "
        "weekday_avg_cents, trend_factor from main_marts.kpi_revenue_forecast_daily "
        "order by days_ahead"
    )


def load_payments(start: date, end: date) -> pd.DataFrame:
    return query(
        "select source_type, payments, amount_collected_cents, processing_fee_cents, "
        "pct_of_collected from rp_payments_range(?, ?) order by amount_collected_cents desc",
        (start, end),
    )


def load_items(start: date, end: date) -> pd.DataFrame:
    return query(
        "select variation_id, item_name, variation_name, category_name, is_lottery, "
        "units, orders, net_sales_cents, avg_weekly_units, prior_units, "
        "prior_net_sales_cents, units_change_pct, net_sales_change_pct, units_all_time, "
        "first_sold_at, last_sold_at from rp_items_range(?, ?) order by units desc",
        (start, end),
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


def full_history_bounds(coverage: pd.Series) -> tuple[date, date]:
    """The widest window there is — used for anything that must not be windowed."""
    return coverage["first_sale_date"], coverage["last_sale_date"]


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


CUSTOM = "Custom"


def _as_date(value) -> date:
    """DuckDB DATEs arrive as pandas Timestamps; the pickers want real dates."""
    return value.date() if isinstance(value, pd.Timestamp) else value


def _select_preset(periods: pd.DataFrame) -> None:
    """Preset clicked: adopt its bounds, and push them into the date picker.

    The date input owns its own widget state once it has a key, so setting the
    canonical window alone would leave the two controls disagreeing on screen.
    Writing the widget's state here is what keeps them in step.
    """
    label = st.session_state.get("rp_preset")
    if not label or label == CUSTOM:
        return
    row = periods[periods["period_label"] == label].iloc[0]
    start, end = _as_date(row["period_start"]), _as_date(row["period_end"])
    st.session_state["rp_window"] = (start, end)
    st.session_state["rp_dates"] = (start, end)


def _select_dates() -> None:
    """Dates edited by hand: adopt them and drop the preset highlight."""
    value = st.session_state.get("rp_dates")
    # While the user is mid-selection the range input reports a single date.
    # Committing that would collapse the window to one day under their cursor.
    if not value or len(value) != 2:
        return
    st.session_state["rp_window"] = (_as_date(value[0]), _as_date(value[1]))
    st.session_state["rp_preset"] = CUSTOM


def render_window_control(periods: pd.DataFrame, coverage: pd.Series):
    """The control that drives the entire page. Sticky, so it is never scrolled away.

    Returns the selected window, the label to describe it by, and a slot to
    write the resolved-window caption into. The caption is deferred because it
    reports figures that only exist once the window has been queried, but it
    belongs *inside* the sticky bar: knowing which window you are looking at
    matters exactly as much as being able to change it, and a caption that
    scrolls away leaves the numbers below it unlabelled.

    Presets and the date range are two faces of one piece of state: a preset
    writes its bounds into the range input, and editing the range demotes the
    preset to "Custom".
    """
    first, last = _as_date(coverage["first_sale_date"]), _as_date(coverage["last_sale_date"])

    if "rp_window" not in st.session_state:
        default = periods[periods["period_label"] == DEFAULT_PERIOD]
        row = (default if not default.empty else periods).iloc[0]
        st.session_state["rp_window"] = (_as_date(row["period_start"]), _as_date(row["period_end"]))
        st.session_state["rp_preset"] = row["period_label"]
        st.session_state["rp_dates"] = st.session_state["rp_window"]

    with st.container(key="rp-window-bar"):
        left, right = st.columns([3, 2], vertical_alignment="center")
        with left:
            st.segmented_control(
                "Period",
                options=[*periods["period_label"].tolist(), CUSTOM],
                key="rp_preset",
                on_change=_select_preset,
                args=(periods,),
                label_visibility="collapsed",
                help="Changes every number on the page. Each window is compared "
                "against the same length of time immediately before it.",
            )
        with right:
            st.date_input(
                "Date range",
                key="rp_dates",
                on_change=_select_dates,
                # Bounded by the data: a window outside the extract can only
                # produce an empty page that looks like a broken dashboard.
                min_value=first,
                max_value=last,
                # st.date_input only accepts numeric patterns; US order to
                # match the rest of the page's date formatting.
                format="MM/DD/YYYY",
                label_visibility="collapsed",
                help=f"Any range between {first:%b %-d, %Y} and {last:%b %-d, %Y}.",
            )
        caption_slot = st.empty()

    start, end = st.session_state["rp_window"]
    label = st.session_state.get("rp_preset") or CUSTOM
    return start, end, label, caption_slot


def window_caption(summary: pd.Series, label: str) -> str:
    """One line naming the window on screen and what it is measured against."""
    start, end = summary["period_start"], summary["period_end"]
    days = int(summary["period_days"])
    span = f"**{start:%b %-d, %Y} – {end:%b %-d, %Y}** · {days:,} days"
    if label != CUSTOM and label:
        span = f"**{label}** · {start:%b %-d} – {end:%b %-d, %Y}"

    if summary["prior_window_complete"]:
        return (
            f"{span} · compared with the previous {days:,} days "
            f"({summary['prior_start']:%b %-d} – {summary['prior_end']:%b %-d, %Y})"
        )
    return f"{span} · no comparison — not enough history behind this window yet"


# --- tab: overview ---------------------------------------------------------


def _prior_note(summary: pd.Series, period: pd.Series, field: str, fmt) -> str:
    """'Previous 30 days: $103,555.00' — the number behind the percentage.

    A delta of -0.9% is only actionable next to what it is -0.9% *of*. This is
    the figure a shopkeeper actually wants, and it replaces the axis-less spark
    line that used to occupy this space: that line had no scale, no baseline
    and no labels, so it could show a shape but never answer a question.
    """
    if not period["prior_window_complete"] or pd.isna(summary[field]):
        return " No comparable earlier window in the data yet."
    return f" Previous {int(period['period_days'])} days: {fmt(summary[field])}."


def render_headline(summary: pd.Series, daily: pd.DataFrame, period: pd.Series) -> None:
    # Every tile is a number, its change, and the number it changed from. The
    # trend itself belongs in "Sales by day" below, where it has axes, a scale
    # and a 7-day average to read it against.
    row1 = st.columns(4)
    row1[0].metric(
        "Sales",
        dollars(summary["net_sales_cents"]),
        delta=change(summary["net_sales_change_pct"]),
        border=True,
        height=TILE_COMPACT,
        help="After discounts, before tax."
        + _prior_note(summary, period, "prior_net_sales_cents", dollars),
    )
    row1[1].metric(
        "Sales count",
        f"{int(summary['orders']):,}",
        delta=change(summary["orders_change_pct"]),
        border=True,
        height=TILE_COMPACT,
        help="Number of transactions rung up."
        + _prior_note(summary, period, "prior_orders", lambda v: f"{int(v):,}"),
    )
    row1[2].metric(
        "Average sale",
        dollars(summary["avg_order_value_cents"]),
        border=True,
        height=TILE_COMPACT,
        help="Sales divided by transactions — what a typical basket is worth.",
    )
    row1[3].metric(
        "Items sold",
        f"{int(summary['units']):,}",
        delta=change(summary["units_change_pct"]),
        border=True,
        height=TILE_COMPACT,
        help=f"{summary['units_per_order']:.2f} items per transaction on average."
        + _prior_note(summary, period, "prior_units", lambda v: f"{int(v):,}"),
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
        #
        # The tick step is computed rather than left to Vega's overlap
        # detection. Vega measures label widths on a canvas at render time; the
        # webfont loads asynchronously, so it measures in the fallback face,
        # concludes the labels fit, and then the real font swaps in and they
        # collide. Deciding the step from the number of days is independent of
        # which font happens to have loaded.
        step = max(1, -(-len(window) // MAX_DATE_LABELS))
        x_axis = alt.Axis(
            tickCount={"interval": "day", "step": step},
            format="%b %-d",
            labelOverlap="greedy",
        )
        x = alt.X("sale_date:T", title=None, axis=x_axis)
        # Axis titles name the measure. The dollar format alone tells you it
        # is money, not which money — net of discounts, before tax.
        y = alt.Y("net_sales:Q", title="Net sales", axis=alt.Axis(format="$,.0f"))

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
                x=alt.X("net_sales:Q", title="Net sales", axis=alt.Axis(format="$,.0f", grid=True)),
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


def render_when_you_sell(popular: pd.DataFrame, p: design.Palette) -> None:
    """Busyness by hour for one weekday, in the shape Google's Popular Times uses.

    The two charts this replaced each answered half a question — takings per
    weekday, and takings per hour across all days. Neither told you whether to
    put a second person on the till at 5pm on a Friday, because a Friday
    evening and a Tuesday evening were averaged into the same bar.
    """
    with st.container(border=True, height="stretch"):
        st.subheader(
            "Popular times",
            help="Transactions per hour on a typical day of the week, in your store's "
            "own local time. Bars are scaled against that day's own busiest hour, so a "
            "quiet Monday is still readable next to a busy Saturday.",
        )
        if popular.empty:
            st.info("No sales in this period.")
            return

        days = [d for d in WEEKDAY_ORDER if d in set(popular["day_name"])]
        busiest = (
            popular.groupby("day_name", as_index=False)["orders"].sum().sort_values("orders")
        )
        default_day = busiest.iloc[-1]["day_name"] if not busiest.empty else days[0]

        chosen = st.segmented_control(
            "Day",
            options=days,
            default=default_day,
            key="popular_day",
            label_visibility="collapsed",
            format_func=lambda d: d[:3],
        )
        day = chosen or default_day
        frame = popular[popular["day_name"] == day]
        if frame.empty:
            st.info(f"No sales on a {day} in this period.")
            return

        # Every hour the store trades in any day of the week, so switching days
        # doesn't reflow the axis and the shapes stay comparable.
        span = range(int(popular["hour_of_day"].min()), int(popular["hour_of_day"].max()) + 1)
        frame = (
            frame.set_index("hour_of_day")
            .reindex(span)
            .assign(
                hour_of_day=list(span),
                day_name=day,
            )
            .fillna({"orders": 0, "orders_per_day": 0, "busyness_pct": 0, "net_sales_cents": 0})
            .reset_index(drop=True)
        )
        frame["net_sales"] = frame["net_sales_cents"] / 100
        peak_hour = int(frame.loc[frame["busyness_pct"].idxmax(), "hour_of_day"])

        bars = (
            alt.Chart(frame)
            .mark_bar(
                size=bar_width(len(frame), span=560),
                cornerRadiusEnd=design.CORNER_RADIUS,
            )
            .encode(
                x=alt.X(
                    "hour_of_day:O",
                    title="Hour of day",
                    axis=alt.Axis(labelAngle=0, labelExpr=HOUR_LABEL),
                ),
                y=alt.Y(
                    "busyness_pct:Q",
                    title="Busyness",
                    scale=alt.Scale(domain=[0, 100]),
                    axis=alt.Axis(format="d", values=[0, 25, 50, 75, 100]),
                ),
                # The peak hour is the one thing to take away, so it is the only
                # bar in the second categorical slot rather than a caption.
                color=alt.condition(
                    alt.datum.hour_of_day == peak_hour,
                    alt.value(p.series_2),
                    alt.value(p.series_1),
                ),
                tooltip=[
                    alt.Tooltip("hour_of_day:O", title="Hour"),
                    alt.Tooltip("orders_per_day:Q", title="Transactions (typical)", format=".1f"),
                    alt.Tooltip("busyness_pct:Q", title="Busyness %", format="d"),
                    money_tip("net_sales:Q", "Sales in window"),
                ],
            )
            .properties(height=CHART_SHORT + 40)
        )
        chart(bars)

        observed = int(frame["days_observed"].max()) if frame["days_observed"].notna().any() else 0
        typical = frame.loc[frame["hour_of_day"] == peak_hour, "orders_per_day"].iloc[0]
        note = (
            f"Busiest around **{_hour_label(peak_hour)}** — about **{typical:.0f} transactions** "
            f"in that hour on a typical {day}."
        )
        if observed <= 1:
            # One observation is not a typical anything; say so rather than let
            # a single day masquerade as a pattern.
            note += f" Based on only {observed} {day} in this window — widen the range."
        else:
            note += f" Averaged over {observed} {day}s."
        st.caption(note)


def render_overview(
    summary: pd.Series,
    daily: pd.DataFrame,
    categories: pd.DataFrame,
    popular: pd.DataFrame,
    period: pd.Series,
    p: design.Palette,
) -> None:
    render_headline(summary, daily, period)
    render_daily_sales(daily, period, p)
    left, right = st.columns(2)
    with left:
        render_categories(categories)
    with right:
        render_when_you_sell(popular, p)


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


def _seller_board(frame: pd.DataFrame, title: str, help_text: str, empty: str, p) -> None:
    """A ranked bar of top sellers by units, with revenue on the tooltip."""
    with st.container(border=True, height="stretch"):
        st.subheader(title, help=help_text)
        if frame.empty:
            st.info(empty)
            return
        top = frame.nlargest(10, "units").copy()
        top["label"] = top.apply(item_label, axis=1)
        top["net_sales"] = top["net_sales_cents"] / 100
        bars = (
            alt.Chart(top)
            .mark_bar(size=design.BAR_MAX_WIDTH, cornerRadiusEnd=design.CORNER_RADIUS)
            .encode(
                x=alt.X("units:Q", title="Units sold", axis=alt.Axis(format=",.0f", grid=True)),
                y=alt.Y(
                    "label:N",
                    sort="-x",
                    title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=200, grid=False),
                ),
                tooltip=[
                    alt.Tooltip("label:N", title="Item"),
                    alt.Tooltip("category_name:N", title="Category"),
                    alt.Tooltip("units:Q", title="Units", format=",.0f"),
                    money_tip("net_sales:Q", "Sales"),
                ],
            )
            .properties(height=CHART_TALL)
        )
        chart(bars)


def render_top_sellers(items: pd.DataFrame, p: design.Palette) -> None:
    """Two boards: merchandise you can act on, and lottery on its own.

    Splitting these is the whole point. Scratchers and lotto outsell every real
    product every single week — the customer picks the store, not the product,
    and volume follows the jackpot. Ranked together they occupy the entire top
    of any "best sellers" list and bury the things a buying decision could
    actually be made about. They are still real revenue, so they get their own
    board rather than being filtered away.

    `is_lottery` comes from dim_item, driven by the `lottery_categories` var,
    so this is a filter on a published column rather than category names
    re-typed in the dashboard.
    """
    merch = items[~items["is_lottery"]]
    lottery = items[items["is_lottery"]]

    left, right = st.columns([3, 2])
    with left:
        _seller_board(
            merch,
            "Top sellers",
            "Your best-moving stock, with lottery and scratchers held out so they "
            "don't crowd out everything you actually choose to buy.",
            "Nothing but lottery sold in this window.",
            p,
        )
    with right:
        _seller_board(
            lottery,
            "Lottery & scratchers",
            "Kept separate because these always top the list on units and are driven "
            "by jackpots rather than anything on your shelves.",
            "No lottery sales in this window.",
            p,
        )

    if not lottery.empty:
        share = lottery["net_sales_cents"].sum() / max(items["net_sales_cents"].sum(), 1) * 100
        st.caption(
            f"Lottery and scratchers are **{share:.0f}%** of item revenue in this window "
            f"({dollars(lottery['net_sales_cents'].sum())} across "
            f"{len(lottery):,} products)."
        )


def render_items(
    items: pd.DataFrame,
    weekly: pd.DataFrame,
    forecast: pd.DataFrame,
    period: pd.Series,
    p: design.Palette,
) -> None:
    if items.empty:
        st.info("Nothing sold in this period.")
        return

    render_top_sellers(items, p)

    st.markdown("---")
    st.subheader(
        "Search every item",
        help="How much of each item sold in the selected period, how that compares with "
        "the previous equivalent stretch, and what it averages per week.",
    )
    view = item_filters(items)
    render_item_table(view, len(items), sparklines(weekly), period)
    render_item_detail(view, weekly, forecast, p)


# --- tab: forecast ---------------------------------------------------------


def render_forecast(horizons: pd.DataFrame, daily_forecast: pd.DataFrame,
                    daily: pd.DataFrame, p: design.Palette) -> None:
    """Expected takings, not expected units.

    The previous version of this tab projected units for four thousand
    individual items. That answers a reordering question, not a cash one — and
    summing four thousand per-item trend lines compounds every one of their
    errors into the total. Money is forecast directly from daily takings
    instead, which is both the question actually being asked and the series
    with the most signal in it.

    Always full history, so it does not follow the window control above:
    forecasting next month from a 7-day window would fit a trend to one week.
    """
    st.subheader(
        "What you can expect to take",
        help="Projected from your own daily takings: the average for that weekday over "
        "the last 8 weeks, adjusted by how the last 4 weeks compare with the 4 before "
        "them. Uses full history, so it doesn't follow the date range above.",
    )
    if horizons.empty or daily_forecast.empty:
        st.info("Not enough daily sales history to project from yet.")
        return

    row = horizons.iloc[0]
    tiles = st.columns(len(horizons))
    for tile, (_, h) in zip(tiles, horizons.iterrows()):
        tile.metric(
            h["horizon_label"],
            dollars(h["forecast_net_sales_cents"]),
            border=True,
            height=TILE_COMPACT,
            help=f"{h['period_start']:%b %-d} – {h['period_end']:%b %-d, %Y} · "
            f"about {dollars(h['forecast_daily_avg_cents'])} a day.",
        )

    # Accuracy up front, not in a footnote. A projection that doesn't say how
    # wrong it usually is invites more trust than it has earned.
    beats = row["points_better_than_baseline"] > 0
    verdict = (
        f"Typically within **{dollars(row['mae_cents'])} a day** "
        f"({row['wape_pct']:.1f}% of takings) when tested on the last "
        f"{int(row['backtest_days'])} days it had never seen."
    )
    if beats:
        verdict += (
            f" That beats a flat daily average, which was out by "
            f"{row['baseline_wape_pct']:.1f}% — {row['points_better_than_baseline']:.1f} "
            "points worse."
        )
    else:
        # Saying so is the point of measuring it.
        verdict += (
            f" A flat daily average scored {row['baseline_wape_pct']:.1f}%, so the "
            "weekday model is **not** earning its complexity here — treat these as a "
            "rough guide."
        )
    st.caption(verdict)

    with st.container(border=True):
        st.subheader(
            "Day by day",
            help="Each day's projection against what you actually took on that weekday "
            "over recent weeks. Weekends are worth more than midweek, which is why the "
            "line moves in a repeating shape rather than trending smoothly.",
        )
        recent = daily.tail(28).assign(
            value=lambda f: f["net_sales_cents"] / 100,
            series="Actual",
        )[["sale_date", "value", "series"]].rename(columns={"sale_date": "day"})
        ahead = daily_forecast.assign(
            value=lambda f: f["forecast_net_sales_cents"] / 100,
            series="Forecast",
        )[["forecast_date", "value", "series"]].rename(columns={"forecast_date": "day"})
        combined = pd.concat([recent, ahead], ignore_index=True)
        combined["day"] = pd.to_datetime(combined["day"])

        scale = alt.Scale(domain=["Actual", "Forecast"], range=[p.series_1, p.series_2])
        line = (
            alt.Chart(combined)
            .mark_line(strokeWidth=design.LINE_WIDTH, point=False)
            .encode(
                x=alt.X(
                    "day:T",
                    title=None,
                    axis=alt.Axis(format="%b %-d", labelOverlap="greedy"),
                ),
                y=alt.Y("value:Q", title="Net sales", axis=alt.Axis(format="$,.0f")),
                # Colour alone separates actual from projected: two series, and
                # two is exactly what the categorical palette is validated for.
                color=alt.Color("series:N", scale=scale, legend=alt.Legend(title=None)),
                tooltip=[
                    alt.Tooltip("day:T", title="Day", format="%a %b %-d"),
                    money_tip("value:Q", "Net sales"),
                    alt.Tooltip("series:N", title=""),
                ],
            )
            .properties(height=CHART_TALL)
        )
        chart(line)
        st.caption(
            f"Last 28 days actual, then {len(daily_forecast)} days projected. "
            f"Trend factor **{row['trend_factor']:.3f}** — the last 4 weeks against the 4 "
            "before them, clamped so one freak fortnight can't run away with it."
        )


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
                        "gross_profit:Q", title="Gross profit", axis=alt.Axis(format="$,.0f", grid=True)
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

    # Loaded before the control, because the control needs the extract's bounds
    # to constrain the date picker.
    periods = load_periods()
    coverage = load_coverage()

    render_header(coverage, p)
    render_sidebar(coverage)
    start, end, label, caption_slot = render_window_control(periods, coverage)

    with st.spinner("Loading…"):
        summary = load_summary(start, end)
        daily = load_daily()
        categories = load_categories(start, end)
        popular = load_popular_times(start, end)
        payments = load_payments(start, end)
        items = load_items(start, end)
        weekly = load_weekly()
        forecast = load_forecast()
        revenue_horizons = load_revenue_forecast()
        revenue_daily = load_revenue_forecast_daily()

    caption_slot.caption(window_caption(summary, label))

    # The summary row already carries the window's bounds, length and whether
    # its prior window is fully covered, so it doubles as the window descriptor
    # every section needs. One object, one source.
    window = summary.copy()
    window["period_label"] = label

    overview_tab, items_tab, forecast_tab, back_office_tab = st.tabs(
        ["Overview", "Items", "Forecast", "Back office"]
    )
    with overview_tab:
        render_overview(summary, daily, categories, popular, window, p)
    with items_tab:
        render_items(items, weekly, forecast, window, p)
    with forecast_tab:
        render_forecast(revenue_horizons, revenue_daily, daily, p)
    with back_office_tab:
        render_back_office(payments)


if __name__ == "__main__":
    main()
