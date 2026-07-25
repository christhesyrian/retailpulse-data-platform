"""RetailPulse KPI dashboard (Streamlit).

Reads the Gold DuckDB warehouse read-only and renders the tested dbt KPI
models. It runs no SQL business logic of its own — every number here comes
from a `kpi_*` model in dbt/models/marts/kpi/, so the dashboard and the
tested warehouse can never disagree.

Run with `make dashboard` (or `streamlit run dashboard/app.py`).
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

WAREHOUSE_PATH = Path(
    os.environ.get("RETAILPULSE_WAREHOUSE_PATH", "data/gold/warehouse.duckdb")
)
WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@st.cache_data(ttl=60)
def query(sql: str) -> pd.DataFrame:
    # Open a short-lived read-only connection per query so the dashboard
    # never holds a lock on the warehouse file (dbt can rebuild freely).
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def dollars(cents: float) -> str:
    return f"${cents / 100:,.2f}"


def units(value: float) -> str:
    return f"{value:,.0f}"


def trend(pct: float) -> str:
    if pd.isna(pct):
        return "—"
    return f"{pct:+.0f}%"


def main() -> None:
    st.set_page_config(page_title="RetailPulse KPIs", page_icon="🛒", layout="wide")
    st.title("🛒 RetailPulse — Sales KPI Dashboard")

    if not WAREHOUSE_PATH.exists():
        st.error(
            f"Warehouse not found at `{WAREHOUSE_PATH}`.\n\n"
            "Build it first:\n\n"
            "```\nmake extract-sandbox   # or: python3 scripts/generate_synthetic_bronze.py data/bronze\n"
            "make silver\nmake dbt-build\n```"
        )
        return

    st.caption(
        "Every metric comes from a tested dbt KPI model. The committed public demo uses "
        "synthetic data; when this warehouse is built from your live Square store these are "
        "your real figures — keep this local and don't publish them."
    )

    summary = query("select * from main_marts.kpi_summary").iloc[0]

    # --- Headline KPI tiles -------------------------------------------------
    row1 = st.columns(4)
    row1[0].metric("Net Sales (ex-tax)", dollars(summary["net_sales_cents"]))
    row1[1].metric("Orders", f"{int(summary['orders']):,}")
    row1[2].metric("Avg Order Value", dollars(summary["avg_order_value_cents"]))
    row1[3].metric("Units / Order", f"{summary['units_per_order']:.2f}")

    row2 = st.columns(4)
    row2[0].metric("Discounts", dollars(summary["discount_cents"]))
    row2[1].metric("Tax Collected", dollars(summary["tax_cents"]))
    row2[2].metric("Processing Fees", dollars(summary["processing_fee_cents"]))
    row2[3].metric("Total Collected", dollars(summary["collected_cents"]))

    # --- Margin tiles (only when vendor costs are on file) ------------------
    # DB nulls arrive as pandas NaN (a float), so `is not None` isn't enough —
    # use pd.notna so a store with no costs simply doesn't see these tiles.
    gross_profit = summary["gross_profit_cents"]
    margin_pct = summary["gross_margin_pct"]
    coverage = summary["cost_coverage_pct"]
    if pd.notna(gross_profit):
        row3 = st.columns(4)
        row3[0].metric("COGS", dollars(summary["cogs_cents"]))
        row3[1].metric("Gross Profit", dollars(gross_profit))
        row3[2].metric("Gross Margin", f"{margin_pct:.1f}%" if pd.notna(margin_pct) else "—")
        row3[3].metric(
            "Cost Coverage",
            f"{coverage:.0f}%" if pd.notna(coverage) else "—",
            help="Share of net sales for which a vendor cost is on file. "
            "Margin is computed only over these lines.",
        )

    # --- Reconciliation status ---------------------------------------------
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
        st.success(
            f"✅ {tie:,} of {total_orders:,} orders reconcile to payments "
            f"({matched:,} to the cent, {overpaid:,} paid over — tips)."
        )
    notes = []
    if short:
        notes.append(f"{short:,} order(s) collected **less** than recorded sales (refunds / comps / unpaid)")
    if pay_no_order:
        notes.append(
            f"{pay_no_order:,} payment(s) have no matching order in this pull "
            "(outside the date window, or a location whose orders weren't extracted)"
        )
    if notes:
        st.caption("Reconciliation notes: " + "; ".join(notes) + ".")

    st.divider()

    # --- Item sales (the core question: what sells, how much, trending) ------
    st.subheader("Item sales")
    st.caption(
        "Units sold per item — this week so far, last complete week, this month, plus a "
        "4-week average and week-over-week trend. Sortable; click a column header."
    )
    items = query(
        "select item_name, category_name, units_this_week, units_last_week, units_this_month, "
        "avg_weekly_units_4wk, wow_trend_pct, units_total, last_sold_at "
        "from main_marts.kpi_item_sales order by units_total desc"
    )
    if items.empty:
        st.info("No item sales yet.")
    else:
        item_display = pd.DataFrame(
            {
                "Item": items["item_name"],
                "Category": items["category_name"].fillna("—"),
                "This week": items["units_this_week"].map(units),
                "Last week": items["units_last_week"].map(units),
                "This month": items["units_this_month"].map(units),
                "Avg/wk (4wk)": items["avg_weekly_units_4wk"].map(lambda v: f"{v:.1f}"),
                "WoW": items["wow_trend_pct"].map(trend),
                "Total sold": items["units_total"].map(units),
                "Last sold": items["last_sold_at"],
            }
        )
        st.dataframe(item_display, hide_index=True, use_container_width=True)

    st.divider()

    # --- Forecast: projected units for upcoming weeks -----------------------
    st.subheader("Sales forecast — next 4 weeks")
    st.caption(
        "Projected units per item per week, from a simple linear trend over recent weeks. "
        "These are estimates; accuracy grows with history, and calendar seasonality "
        "(holidays) is not modeled yet."
    )
    fc = query(
        "select item_name, forecast_week_start, weeks_ahead, forecast_units "
        "from main_marts.kpi_item_forecast"
    )
    if fc.empty:
        st.info("Not enough weekly sales history to forecast yet — needs at least a couple of weeks.")
    else:
        totals = (
            fc.groupby(["forecast_week_start", "weeks_ahead"], as_index=False)["forecast_units"]
            .sum()
            .sort_values("weeks_ahead")
        )
        tcols = st.columns(len(totals))
        for i, (_, r) in enumerate(totals.iterrows()):
            label = "Next week" if int(r["weeks_ahead"]) == 1 else f"In {int(r['weeks_ahead'])} weeks"
            tcols[i].metric(
                f"{label}", f"{int(r['forecast_units']):,} units",
                help=f"Week starting {r['forecast_week_start']}",
            )
        st.caption("Projected units by item and week (read the column for the week you care about):")
        pivot = (
            fc.pivot_table(
                index="item_name", columns="forecast_week_start",
                values="forecast_units", aggfunc="sum",
            )
            .fillna(0)
            .astype(int)
        )
        pivot.index.name = "Item"
        st.dataframe(pivot, use_container_width=True)

    st.divider()

    # --- Daily net sales ----------------------------------------------------
    st.subheader("Daily net sales")
    daily = query(
        "select sale_date, net_sales_cents, orders from main_marts.kpi_daily_sales order by sale_date"
    )
    daily["Net sales ($)"] = daily["net_sales_cents"] / 100
    daily_chart = (
        alt.Chart(daily)
        .mark_line(point=True)
        .encode(
            x=alt.X("sale_date:T", title="Date"),
            y=alt.Y("Net sales ($):Q", title="Net sales ($)"),
            tooltip=["sale_date:T", "Net sales ($):Q", "orders:Q"],
        )
        .properties(height=280)
    )
    st.altair_chart(daily_chart, use_container_width=True)

    # --- Category + weekday side by side ------------------------------------
    left, right = st.columns(2)

    with left:
        st.subheader("Net sales by category")
        cat = query(
            "select category_name, net_sales_cents, pct_of_net_sales "
            "from main_marts.kpi_sales_by_category order by net_sales_cents desc"
        )
        cat["Net sales ($)"] = cat["net_sales_cents"] / 100
        cat_chart = (
            alt.Chart(cat)
            .mark_bar()
            .encode(
                x=alt.X("Net sales ($):Q", title="Net sales ($)"),
                y=alt.Y("category_name:N", sort="-x", title=None),
                tooltip=["category_name:N", "Net sales ($):Q", "pct_of_net_sales:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(cat_chart, use_container_width=True)

    with right:
        st.subheader("Net sales by weekday")
        wd = query(
            "select day_name, net_sales_cents, orders from main_marts.kpi_sales_by_weekday "
            "order by day_of_week"
        )
        wd["Net sales ($)"] = wd["net_sales_cents"] / 100
        wd_chart = (
            alt.Chart(wd)
            .mark_bar()
            .encode(
                x=alt.X("day_name:N", sort=WEEKDAY_ORDER, title=None),
                y=alt.Y("Net sales ($):Q", title="Net sales ($)"),
                tooltip=["day_name:N", "Net sales ($):Q", "orders:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(wd_chart, use_container_width=True)

    # --- Hour of day + payment mix ------------------------------------------
    left2, right2 = st.columns(2)

    with left2:
        st.subheader("Net sales by hour of day")
        hour = query(
            "select hour_of_day, net_sales_cents, orders from main_marts.kpi_sales_by_hour "
            "order by hour_of_day"
        )
        hour["Net sales ($)"] = hour["net_sales_cents"] / 100
        hour_chart = (
            alt.Chart(hour)
            .mark_bar()
            .encode(
                x=alt.X("hour_of_day:O", title="Hour"),
                y=alt.Y("Net sales ($):Q", title="Net sales ($)"),
                tooltip=["hour_of_day:O", "Net sales ($):Q", "orders:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(hour_chart, use_container_width=True)

    with right2:
        st.subheader("Payment method mix")
        pay = query("select * from main_marts.kpi_payment_methods order by amount_collected_cents desc")
        pay_display = pd.DataFrame(
            {
                "Method": pay["source_type"],
                "Payments": pay["payments"].astype(int),
                "Collected": (pay["amount_collected_cents"] / 100).map(lambda v: f"${v:,.2f}"),
                "Fees": (pay["processing_fee_cents"] / 100).map(lambda v: f"${v:,.2f}"),
                "% of collected": pay["pct_of_collected"].map(lambda v: f"{v:.1f}%"),
            }
        )
        st.dataframe(pay_display, hide_index=True, use_container_width=True)

    # --- Gross margin (optional — only shown when vendor costs are on file) --
    margin = query(
        "select category_name, net_sales_cents, gross_profit_cents, gross_margin_pct, "
        "line_cost_coverage_pct from main_marts.kpi_margin_by_category "
        "where gross_profit_cents is not null order by gross_profit_cents desc"
    )
    if not margin.empty:
        st.divider()
        st.subheader("Gross profit & margin by category")
        st.caption(
            "Gross profit = net sales − cost of goods sold, using operator-maintained vendor "
            "costs. Only lines with a known cost are included; this is true profit, not revenue."
        )
        mleft, mright = st.columns([3, 2])
        with mleft:
            margin["Gross profit ($)"] = margin["gross_profit_cents"] / 100
            margin_chart = (
                alt.Chart(margin)
                .mark_bar()
                .encode(
                    x=alt.X("Gross profit ($):Q", title="Gross profit ($)"),
                    y=alt.Y("category_name:N", sort="-x", title=None),
                    tooltip=["category_name:N", "Gross profit ($):Q", "gross_margin_pct:Q"],
                )
                .properties(height=260)
            )
            st.altair_chart(margin_chart, use_container_width=True)
        with mright:
            margin_display = pd.DataFrame(
                {
                    "Category": margin["category_name"],
                    "Net sales": (margin["net_sales_cents"] / 100).map(lambda v: f"${v:,.0f}"),
                    "Gross profit": (margin["gross_profit_cents"] / 100).map(lambda v: f"${v:,.0f}"),
                    "Margin": margin["gross_margin_pct"].map(
                        lambda v: f"{v:.1f}%" if pd.notna(v) else "—"
                    ),
                }
            )
            st.dataframe(margin_display, hide_index=True, use_container_width=True)

    # --- Inventory position (optional — only shown when stock data exists) ---
    inv = query(
        "select item_name, category_name, quantity_on_hand, units_sold_30d, "
        "days_of_inventory, stock_status from main_marts.kpi_inventory_position "
        "order by days_of_inventory nulls last"
    )
    if not inv.empty:
        st.divider()
        st.subheader("Inventory position & reorder signal")
        st.caption(
            "Days of inventory = on-hand ÷ average daily units sold (trailing 30 days). "
            "Reorder heuristic is a fixed threshold, not a demand forecast (that's a later milestone)."
        )
        reorder_count = int((inv["stock_status"] == "reorder_soon").sum())
        if reorder_count:
            st.warning(f"⚠️ {reorder_count} item(s) flagged **reorder_soon** (under ~7 days of stock).")
        inv_display = pd.DataFrame(
            {
                "Item": inv["item_name"],
                "Category": inv["category_name"],
                "On hand": inv["quantity_on_hand"].map(lambda v: f"{v:,.0f}"),
                "Sold (30d)": inv["units_sold_30d"].map(lambda v: f"{v:,.0f}"),
                "Days of inventory": inv["days_of_inventory"].map(
                    lambda v: f"{v:.1f}" if pd.notna(v) else "—"
                ),
                "Status": inv["stock_status"],
            }
        )
        st.dataframe(inv_display, hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
