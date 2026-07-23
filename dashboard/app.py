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
        "Demonstration data only — generated synthetic records or Square **Sandbox** data. "
        "No real customer, payment, or business information is shown. "
        "Every metric is sourced from a tested dbt KPI model."
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

    # --- Margin tiles (M5: vendor costs unlock true profit) -----------------
    gross_profit = summary["gross_profit_cents"]
    margin_pct = summary["gross_margin_pct"]
    coverage = summary["cost_coverage_pct"]
    if gross_profit is not None:
        row3 = st.columns(4)
        row3[0].metric("COGS", dollars(summary["cogs_cents"]))
        row3[1].metric("Gross Profit", dollars(gross_profit))
        row3[2].metric("Gross Margin", f"{margin_pct:.1f}%" if margin_pct is not None else "—")
        row3[3].metric(
            "Cost Coverage",
            f"{coverage:.0f}%" if coverage is not None else "—",
            help="Share of net sales for which a vendor cost is on file. "
            "Margin is computed only over these lines.",
        )

    # --- Reconciliation status ---------------------------------------------
    recon = query(
        "select reconciliation_status, count(*) as n "
        "from main_marts.rpt_order_payment_reconciliation group by 1"
    )
    status_counts = dict(zip(recon["reconciliation_status"], recon["n"]))
    mismatches = int(status_counts.get("mismatch", 0))
    matched = int(status_counts.get("matched", 0))
    if mismatches == 0:
        st.success(
            f"✅ Reconciliation passed — {matched:,} orders tie exactly to payments collected "
            "(net sales + tax = amount collected). No mismatches."
        )
    else:
        st.error(f"⚠️ {mismatches:,} order(s) do not reconcile to their payments.")

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

    st.divider()

    # --- Gross margin (M5) --------------------------------------------------
    st.subheader("Gross profit & margin by category")
    st.caption(
        "Gross profit = net sales − cost of goods sold, using operator-maintained vendor "
        "costs. Only lines with a known cost are included; this is true profit, not revenue."
    )
    margin = query(
        "select category_name, net_sales_cents, gross_profit_cents, gross_margin_pct, "
        "line_cost_coverage_pct from main_marts.kpi_margin_by_category "
        "order by gross_profit_cents desc"
    )
    if margin.empty or margin["gross_profit_cents"].isna().all():
        st.info(
            "No vendor costs on file yet. Generate them with "
            "`python3 scripts/generate_synthetic_vendor_costs.py` (or add your own "
            "`data/input/vendor_costs.csv`) and rebuild."
        )
    else:
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

    st.divider()

    # --- Inventory position (M5) -------------------------------------------
    st.subheader("Inventory position & reorder signal")
    st.caption(
        "Days of inventory = on-hand ÷ average daily units sold (trailing 30 days). "
        "Reorder heuristic is a fixed threshold, not a demand forecast (that's a later milestone)."
    )
    inv = query(
        "select item_name, category_name, quantity_on_hand, units_sold_30d, "
        "days_of_inventory, stock_status from main_marts.kpi_inventory_position "
        "order by days_of_inventory nulls last"
    )
    if inv.empty:
        st.info("No inventory snapshots found. Run an extraction (or the synthetic generator) first.")
    else:
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
