"""Bronze -> Silver normalization.

Reads immutable Bronze JSON, which accumulates one snapshot per
extraction run, and produces deduplicated, flattened Silver tables as
Parquet files under data/silver/ — the local "lake" that dbt-duckdb
reads directly (see dbt/models/staging). Unlike Bronze, Silver output
is derived and safely rebuilt from Bronze on every run; it is not
itself immutable.

Money stays in integer cents; timestamps stay as the UTC strings
Square returns (Gold models cast them). Payment card details are
minimized to brand + last 4 digits — fingerprint and BIN are dropped
here rather than carried into Silver/Gold.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

# (column name, DuckDB type) — explicit typing for the Parquet schema.
LOCATIONS_SCHEMA = [
    ("location_id", "VARCHAR"), ("name", "VARCHAR"), ("status", "VARCHAR"),
    ("timezone", "VARCHAR"), ("currency", "VARCHAR"), ("country", "VARCHAR"),
    ("business_name", "VARCHAR"), ("merchant_id", "VARCHAR"), ("created_at", "VARCHAR"),
]
CATALOG_ITEMS_SCHEMA = [
    ("variation_id", "VARCHAR"), ("item_id", "VARCHAR"), ("item_name", "VARCHAR"),
    ("variation_name", "VARCHAR"), ("category_id", "VARCHAR"), ("category_name", "VARCHAR"),
    ("price_cents", "BIGINT"), ("currency", "VARCHAR"), ("sku", "VARCHAR"),
    ("is_deleted", "BOOLEAN"), ("updated_at", "VARCHAR"),
]
ORDER_LINES_SCHEMA = [
    ("order_id", "VARCHAR"), ("line_item_uid", "VARCHAR"), ("location_id", "VARCHAR"),
    ("catalog_object_id", "VARCHAR"), ("item_name", "VARCHAR"), ("variation_name", "VARCHAR"),
    ("quantity", "VARCHAR"), ("gross_sales_cents", "BIGINT"), ("discount_cents", "BIGINT"),
    ("tax_cents", "BIGINT"), ("net_sales_cents", "BIGINT"), ("currency", "VARCHAR"),
    ("order_state", "VARCHAR"), ("order_created_at", "VARCHAR"),
    ("order_updated_at", "VARCHAR"), ("closed_at", "VARCHAR"),
]
PAYMENTS_SCHEMA = [
    ("payment_id", "VARCHAR"), ("order_id", "VARCHAR"), ("location_id", "VARCHAR"),
    ("amount_cents", "BIGINT"), ("currency", "VARCHAR"), ("status", "VARCHAR"),
    ("source_type", "VARCHAR"), ("card_brand", "VARCHAR"), ("card_last_4", "VARCHAR"),
    ("processing_fee_cents", "BIGINT"), ("created_at", "VARCHAR"), ("updated_at", "VARCHAR"),
]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _amount(money: dict[str, Any] | None) -> int:
    if not money:
        return 0
    return int(money.get("amount", 0))


def _iter_bronze_records(
    bronze_root: Path, entity: str, list_key: str
) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (record, extracted_at) for every `list_key` record across all Bronze pages."""
    entity_root = bronze_root / "square" / entity
    if not entity_root.exists():
        return
    for path in sorted(entity_root.glob("**/*.json")):
        envelope = json.loads(path.read_text(encoding="utf-8"))
        extracted_at = envelope["metadata"]["extracted_at"]
        for record in envelope["payload"].get(list_key, []):
            yield record, extracted_at


def _dedupe_latest(
    records: Iterator[tuple[dict[str, Any], str]],
    id_key: str,
    freshness_key: str | None = None,
) -> list[dict[str, Any]]:
    """Collapse repeated extractions of the same Square object to its most recent version.

    Prefers the record's own freshness field (e.g. Square's `updated_at`)
    when present, falling back to the Bronze `extracted_at` timestamp.
    """
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for record, extracted_at in records:
        object_id = record.get(id_key)
        if object_id is None:
            continue
        raw_freshness = (record.get(freshness_key) if freshness_key else None) or extracted_at
        freshness = _parse_ts(raw_freshness)
        current = latest.get(object_id)
        if current is None or freshness >= current[0]:
            latest[object_id] = (freshness, record)
    return [record for _freshness, record in latest.values()]


def _item_category_id(item_data: dict[str, Any]) -> str | None:
    """Square has used `categories`, `reporting_category`, and legacy `category_id`
    across API versions for the same concept. Check all three, current-first."""
    categories = item_data.get("categories")
    if categories:
        return categories[0].get("id")
    reporting = item_data.get("reporting_category")
    if reporting:
        return reporting.get("id")
    return item_data.get("category_id")


def build_silver_locations(bronze_root: Path) -> list[dict[str, Any]]:
    records = _dedupe_latest(_iter_bronze_records(bronze_root, "locations", "locations"), id_key="id")
    rows = [
        {
            "location_id": loc.get("id"),
            "name": loc.get("name"),
            "status": loc.get("status"),
            "timezone": loc.get("timezone"),
            "currency": loc.get("currency"),
            "country": loc.get("country"),
            "business_name": loc.get("business_name"),
            "merchant_id": loc.get("merchant_id"),
            "created_at": loc.get("created_at"),
        }
        for loc in records
    ]
    rows.sort(key=lambda r: r["location_id"] or "")
    return rows


def build_silver_catalog_items(bronze_root: Path) -> list[dict[str, Any]]:
    by_type: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for record, extracted_at in _iter_bronze_records(bronze_root, "catalog", "objects"):
        by_type.setdefault(record.get("type"), []).append((record, extracted_at))

    items = {
        r["id"]: r
        for r in _dedupe_latest(iter(by_type.get("ITEM", [])), id_key="id", freshness_key="updated_at")
    }
    categories = {
        r["id"]: r
        for r in _dedupe_latest(iter(by_type.get("CATEGORY", [])), id_key="id", freshness_key="updated_at")
    }
    variations = _dedupe_latest(
        iter(by_type.get("ITEM_VARIATION", [])), id_key="id", freshness_key="updated_at"
    )

    rows = []
    for variation in variations:
        var_data = variation.get("item_variation_data", {})
        item_id = var_data.get("item_id")
        item = items.get(item_id, {})
        item_data = item.get("item_data", {})
        category_id = _item_category_id(item_data)
        category = categories.get(category_id, {}) if category_id else {}
        price_money = var_data.get("price_money")

        rows.append(
            {
                "variation_id": variation.get("id"),
                "item_id": item_id,
                "item_name": item_data.get("name"),
                "variation_name": var_data.get("name"),
                "category_id": category_id,
                "category_name": category.get("category_data", {}).get("name"),
                "price_cents": _amount(price_money),
                "currency": (price_money or {}).get("currency"),
                "sku": var_data.get("sku"),
                "is_deleted": bool(variation.get("is_deleted") or item.get("is_deleted", False)),
                "updated_at": variation.get("updated_at"),
            }
        )
    rows.sort(key=lambda r: (r["item_name"] or "", r["variation_name"] or ""))
    return rows


def build_silver_order_lines(bronze_root: Path) -> list[dict[str, Any]]:
    orders = _dedupe_latest(
        _iter_bronze_records(bronze_root, "orders", "orders"), id_key="id", freshness_key="updated_at"
    )
    rows = []
    for order in orders:
        for line in order.get("line_items", []):
            rows.append(
                {
                    "order_id": order.get("id"),
                    "line_item_uid": line.get("uid"),
                    "location_id": order.get("location_id"),
                    "catalog_object_id": line.get("catalog_object_id"),
                    "item_name": line.get("name"),
                    "variation_name": line.get("variation_name"),
                    "quantity": line.get("quantity"),
                    "gross_sales_cents": _amount(line.get("gross_sales_money")),
                    "discount_cents": _amount(line.get("total_discount_money")),
                    "tax_cents": _amount(line.get("total_tax_money")),
                    "net_sales_cents": _amount(line.get("total_money")),
                    "currency": (line.get("total_money") or {}).get("currency"),
                    "order_state": order.get("state"),
                    "order_created_at": order.get("created_at"),
                    "order_updated_at": order.get("updated_at"),
                    "closed_at": order.get("closed_at"),
                }
            )
    rows.sort(key=lambda r: (r["order_id"] or "", r["line_item_uid"] or ""))
    return rows


def build_silver_payments(bronze_root: Path) -> list[dict[str, Any]]:
    payments = _dedupe_latest(
        _iter_bronze_records(bronze_root, "payments", "payments"), id_key="id", freshness_key="updated_at"
    )
    rows = []
    for payment in payments:
        card = (payment.get("card_details") or {}).get("card") or {}
        processing_fee_cents = sum(
            _amount(fee.get("amount_money")) for fee in payment.get("processing_fee") or []
        )
        amount_money = payment.get("amount_money")
        rows.append(
            {
                "payment_id": payment.get("id"),
                "order_id": payment.get("order_id"),
                "location_id": payment.get("location_id"),
                "amount_cents": _amount(amount_money),
                "currency": (amount_money or {}).get("currency"),
                "status": payment.get("status"),
                "source_type": payment.get("source_type"),
                "card_brand": card.get("card_brand"),
                "card_last_4": card.get("last_4"),
                "processing_fee_cents": processing_fee_cents,
                "created_at": payment.get("created_at"),
                "updated_at": payment.get("updated_at"),
            }
        )
    rows.sort(key=lambda r: r["payment_id"] or "")
    return rows


def write_silver_parquet(
    rows: list[dict[str, Any]], path: Path, schema: list[tuple[str, str]]
) -> Path:
    """Write typed rows to a Parquet file via an in-memory DuckDB table.

    DuckDB has native Parquet support, so this needs no separate Arrow/
    pandas dependency — the same `duckdb` package used as the dbt target
    writes the lake files dbt reads.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    column_names = [name for name, _ in schema]
    con = duckdb.connect(":memory:")
    try:
        col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in schema)
        con.execute(f"CREATE TABLE silver ({col_defs})")
        if rows:
            placeholders = ", ".join(["?"] * len(schema))
            values = [tuple(row.get(name) for name in column_names) for row in rows]
            con.executemany(f"INSERT INTO silver VALUES ({placeholders})", values)
        con.execute(f"COPY silver TO '{path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    return path


def run_silver_transform(bronze_root: Path, silver_root: Path) -> dict[str, int]:
    """Rebuild every Silver table from Bronze and return row counts per table."""
    tables: dict[str, tuple[list[dict[str, Any]], list[tuple[str, str]]]] = {
        "locations": (build_silver_locations(bronze_root), LOCATIONS_SCHEMA),
        "catalog_items": (build_silver_catalog_items(bronze_root), CATALOG_ITEMS_SCHEMA),
        "order_lines": (build_silver_order_lines(bronze_root), ORDER_LINES_SCHEMA),
        "payments": (build_silver_payments(bronze_root), PAYMENTS_SCHEMA),
    }
    counts: dict[str, int] = {}
    for name, (rows, schema) in tables.items():
        write_silver_parquet(rows, silver_root / f"{name}.parquet", schema)
        counts[name] = len(rows)
    return counts
