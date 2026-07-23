from datetime import datetime, timezone

import duckdb

from retailpulse.storage import write_raw_page
from retailpulse.transform.silver import (
    build_silver_catalog_items,
    build_silver_inventory,
    build_silver_order_lines,
    build_silver_payments,
    build_silver_locations,
    run_silver_transform,
    write_silver_parquet,
)


def _write(bronze_root, entity, payload, run_id, extracted_at):
    write_raw_page(
        bronze_root, "square", entity, payload, page_number=1, run_id=run_id,
        environment="sandbox", extracted_at=extracted_at,
    )


T1 = datetime(2026, 7, 23, 0, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 23, 1, 0, 0, tzinfo=timezone.utc)


def test_locations_dedupe_keeps_most_recently_extracted(tmp_path):
    _write(
        tmp_path, "locations",
        {"locations": [{"id": "loc-1", "name": "Old Name", "status": "ACTIVE"}]},
        run_id="run-a", extracted_at=T1,
    )
    _write(
        tmp_path, "locations",
        {"locations": [{"id": "loc-1", "name": "New Name", "status": "ACTIVE"}]},
        run_id="run-b", extracted_at=T2,
    )

    rows = build_silver_locations(tmp_path)

    assert len(rows) == 1
    assert rows[0]["name"] == "New Name"


def test_catalog_items_flatten_and_join_category(tmp_path):
    payload = {
        "objects": [
            {
                "type": "CATEGORY", "id": "cat-1", "updated_at": "2026-07-23T00:00:00Z",
                "is_deleted": False, "category_data": {"name": "Beer"},
            },
            {
                "type": "ITEM", "id": "item-1", "updated_at": "2026-07-23T00:00:00Z",
                "is_deleted": False,
                "item_data": {"name": "Sample Lager", "categories": [{"id": "cat-1"}]},
            },
            {
                "type": "ITEM_VARIATION", "id": "var-1", "updated_at": "2026-07-23T00:00:00Z",
                "is_deleted": False,
                "item_variation_data": {
                    "item_id": "item-1", "name": "Regular",
                    "price_money": {"amount": 1299, "currency": "USD"},
                },
            },
        ]
    }
    _write(tmp_path, "catalog", payload, run_id="run-a", extracted_at=T1)

    rows = build_silver_catalog_items(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["item_name"] == "Sample Lager"
    assert row["variation_name"] == "Regular"
    assert row["category_name"] == "Beer"
    assert row["price_cents"] == 1299
    assert row["currency"] == "USD"


def test_catalog_item_without_category_does_not_crash(tmp_path):
    payload = {
        "objects": [
            {
                "type": "ITEM", "id": "item-1", "updated_at": "2026-07-23T00:00:00Z",
                "is_deleted": False, "item_data": {"name": "Orphan Item"},
            },
            {
                "type": "ITEM_VARIATION", "id": "var-1", "updated_at": "2026-07-23T00:00:00Z",
                "is_deleted": False,
                "item_variation_data": {
                    "item_id": "item-1", "name": "Regular",
                    "price_money": {"amount": 500, "currency": "USD"},
                },
            },
        ]
    }
    _write(tmp_path, "catalog", payload, run_id="run-a", extracted_at=T1)

    rows = build_silver_catalog_items(tmp_path)

    assert len(rows) == 1
    assert rows[0]["category_id"] is None
    assert rows[0]["category_name"] is None


def test_order_lines_flatten_multiple_line_items(tmp_path):
    payload = {
        "orders": [
            {
                "id": "order-1", "location_id": "loc-1", "state": "COMPLETED",
                "created_at": "2026-07-23T00:00:00Z", "updated_at": "2026-07-23T00:01:00Z",
                "closed_at": "2026-07-23T00:01:00Z",
                "line_items": [
                    {
                        "uid": "line-a", "catalog_object_id": "var-1", "name": "Item A",
                        "quantity": "2",
                        "gross_sales_money": {"amount": 2000, "currency": "USD"},
                        "total_discount_money": {"amount": 100, "currency": "USD"},
                        "total_tax_money": {"amount": 50, "currency": "USD"},
                        "total_money": {"amount": 1950, "currency": "USD"},
                    },
                    {
                        "uid": "line-b", "catalog_object_id": "var-2", "name": "Item B",
                        "quantity": "1",
                        "gross_sales_money": {"amount": 500, "currency": "USD"},
                        "total_money": {"amount": 500, "currency": "USD"},
                    },
                ],
            }
        ]
    }
    _write(tmp_path, "orders", payload, run_id="run-a", extracted_at=T1)

    rows = build_silver_order_lines(tmp_path)

    assert len(rows) == 2
    row_a = next(r for r in rows if r["line_item_uid"] == "line-a")
    assert row_a["gross_sales_cents"] == 2000
    assert row_a["discount_cents"] == 100
    assert row_a["tax_cents"] == 50
    # Net sales excludes tax: gross (2000) - discount (100) = 1900,
    # NOT total_money (1950, which includes the 50 tax).
    assert row_a["net_sales_cents"] == 1900
    assert row_a["order_state"] == "COMPLETED"
    row_b = next(r for r in rows if r["line_item_uid"] == "line-b")
    assert row_b["discount_cents"] == 0  # absent in source -> defaults to 0, not a crash
    assert row_b["net_sales_cents"] == 500  # gross 500 - discount 0


def test_orders_dedupe_by_updated_at_regardless_of_file_order(tmp_path):
    stale = {
        "orders": [{
            "id": "order-1", "location_id": "loc-1", "state": "OPEN",
            "updated_at": "2026-07-23T00:01:00Z", "line_items": [
                {"uid": "line-a", "name": "Item A", "quantity": "1"},
            ],
        }]
    }
    fresh = {
        "orders": [{
            "id": "order-1", "location_id": "loc-1", "state": "COMPLETED",
            "updated_at": "2026-07-23T00:05:00Z", "line_items": [
                {"uid": "line-a", "name": "Item A", "quantity": "1"},
            ],
        }]
    }
    # Write the fresher snapshot first and the stale one second, to prove
    # dedup goes by the record's own updated_at, not file/extraction order.
    _write(tmp_path, "orders", fresh, run_id="run-b", extracted_at=T2)
    _write(tmp_path, "orders", stale, run_id="run-a", extracted_at=T1)

    rows = build_silver_order_lines(tmp_path)

    assert len(rows) == 1
    assert rows[0]["order_state"] == "COMPLETED"


def test_payments_minimize_card_pii(tmp_path):
    payload = {
        "payments": [
            {
                "id": "pay-1", "order_id": "order-1", "location_id": "loc-1",
                "amount_money": {"amount": 1000, "currency": "USD"},
                "status": "COMPLETED", "source_type": "CARD",
                "updated_at": "2026-07-23T00:00:00Z",
                "card_details": {
                    "card": {
                        "card_brand": "VISA", "last_4": "1234",
                        "fingerprint": "sq-super-secret-fingerprint",
                        "bin": "453275",
                    }
                },
            }
        ]
    }
    _write(tmp_path, "payments", payload, run_id="run-a", extracted_at=T1)

    rows = build_silver_payments(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["card_brand"] == "VISA"
    assert row["card_last_4"] == "1234"
    assert "fingerprint" not in row
    assert "bin" not in row
    assert not any("secret" in str(v) for v in row.values())


def test_inventory_dedup_keeps_latest_by_calculated_at(tmp_path):
    stale = {
        "counts": [{
            "catalog_object_id": "var-1", "catalog_object_type": "ITEM_VARIATION",
            "location_id": "loc-1", "state": "IN_STOCK", "quantity": "10",
            "calculated_at": "2026-07-23T00:00:00Z",
        }]
    }
    fresh = {
        "counts": [{
            "catalog_object_id": "var-1", "catalog_object_type": "ITEM_VARIATION",
            "location_id": "loc-1", "state": "IN_STOCK", "quantity": "3",
            "calculated_at": "2026-07-23T06:00:00Z",
        }]
    }
    # Write fresh first, stale second — dedup must go by calculated_at, not order.
    _write(tmp_path, "inventory", fresh, run_id="run-b", extracted_at=T2)
    _write(tmp_path, "inventory", stale, run_id="run-a", extracted_at=T1)

    rows = build_silver_inventory(tmp_path)

    assert len(rows) == 1
    assert rows[0]["quantity"] == 3.0  # latest count, and typed as a number
    assert rows[0]["catalog_object_id"] == "var-1"


def test_inventory_composite_key_keeps_separate_locations(tmp_path):
    payload = {
        "counts": [
            {"catalog_object_id": "var-1", "location_id": "loc-1", "state": "IN_STOCK",
             "quantity": "5", "calculated_at": "2026-07-23T00:00:00Z"},
            {"catalog_object_id": "var-1", "location_id": "loc-2", "state": "IN_STOCK",
             "quantity": "9", "calculated_at": "2026-07-23T00:00:00Z"},
        ]
    }
    _write(tmp_path, "inventory", payload, run_id="run-a", extracted_at=T1)

    rows = build_silver_inventory(tmp_path)

    # Same variation at two locations must remain two distinct snapshot rows.
    assert len(rows) == 2
    assert {r["location_id"] for r in rows} == {"loc-1", "loc-2"}


def test_write_silver_parquet_round_trip(tmp_path):
    rows = [{"a": "x", "b": 1}, {"a": "y", "b": 2}]
    schema = [("a", "VARCHAR"), ("b", "BIGINT")]
    path = write_silver_parquet(rows, tmp_path / "out.parquet", schema)

    con = duckdb.connect(":memory:")
    result = con.execute(f"SELECT a, b FROM read_parquet('{path.as_posix()}') ORDER BY a").fetchall()
    con.close()

    assert result == [("x", 1), ("y", 2)]


def test_write_silver_parquet_empty_rows_still_creates_typed_file(tmp_path):
    schema = [("a", "VARCHAR"), ("b", "BIGINT")]
    path = write_silver_parquet([], tmp_path / "out.parquet", schema)

    con = duckdb.connect(":memory:")
    result = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()
    con.close()

    assert result == []


def test_run_silver_transform_writes_all_tables(tmp_path):
    bronze_root = tmp_path / "bronze"
    silver_root = tmp_path / "silver"

    _write(bronze_root, "locations", {"locations": [{"id": "loc-1", "name": "L"}]}, "run-a", T1)
    _write(bronze_root, "catalog", {"objects": []}, "run-a", T1)
    _write(bronze_root, "orders", {"orders": []}, "run-a", T1)
    _write(bronze_root, "payments", {"payments": []}, "run-a", T1)
    _write(bronze_root, "inventory", {"counts": []}, "run-a", T1)

    counts = run_silver_transform(bronze_root, silver_root)

    assert counts == {
        "locations": 1, "catalog_items": 0, "order_lines": 0,
        "payments": 0, "inventory_snapshots": 0,
    }
    for name in ("locations", "catalog_items", "order_lines", "payments", "inventory_snapshots"):
        assert (silver_root / f"{name}.parquet").exists()
