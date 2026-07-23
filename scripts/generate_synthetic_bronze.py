#!/usr/bin/env python3
"""Generate a tiny synthetic Bronze fixture for CI / local dbt dry-runs.

No Square credentials, no network access, no real data — just enough
fake records (one of each entity, properly linked) to exercise every
Silver table and every dbt test (dedup, joins, not-null/unique).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from retailpulse.storage import write_raw_page  # noqa: E402

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/bronze")
RUN_ID = "synthetic"
NOW = datetime.now(timezone.utc).isoformat()


def _write(entity: str, payload: dict) -> None:
    write_raw_page(ROOT, "square", entity, payload, page_number=1, run_id=RUN_ID, environment="sandbox")


def main() -> None:
    _write(
        "locations",
        {
            "locations": [
                {
                    "id": "loc-synthetic", "name": "Synthetic Test Store", "status": "ACTIVE",
                    "timezone": "UTC", "currency": "USD", "country": "US",
                    "business_name": "Synthetic Test Store", "merchant_id": "merch-synthetic",
                    "created_at": NOW,
                }
            ]
        },
    )

    _write(
        "catalog",
        {
            "objects": [
                {
                    "type": "CATEGORY", "id": "cat-synthetic", "updated_at": NOW, "is_deleted": False,
                    "category_data": {"name": "Synthetic Category"},
                },
                {
                    "type": "ITEM", "id": "item-synthetic", "updated_at": NOW, "is_deleted": False,
                    "item_data": {
                        "name": "Synthetic Item",
                        "categories": [{"id": "cat-synthetic"}],
                    },
                },
                {
                    "type": "ITEM_VARIATION", "id": "var-synthetic", "updated_at": NOW, "is_deleted": False,
                    "item_variation_data": {
                        "item_id": "item-synthetic", "name": "Regular",
                        "price_money": {"amount": 999, "currency": "USD"},
                    },
                },
            ]
        },
    )

    _write(
        "orders",
        {
            "orders": [
                {
                    "id": "order-synthetic", "location_id": "loc-synthetic", "state": "COMPLETED",
                    "created_at": NOW, "updated_at": NOW, "closed_at": NOW,
                    "line_items": [
                        {
                            "uid": "line-synthetic", "catalog_object_id": "var-synthetic",
                            "name": "Synthetic Item", "variation_name": "Regular", "quantity": "1",
                            "gross_sales_money": {"amount": 999, "currency": "USD"},
                            "total_money": {"amount": 999, "currency": "USD"},
                        }
                    ],
                }
            ]
        },
    )

    _write(
        "payments",
        {
            "payments": [
                {
                    "id": "pay-synthetic", "order_id": "order-synthetic", "location_id": "loc-synthetic",
                    "amount_money": {"amount": 999, "currency": "USD"}, "status": "COMPLETED",
                    "source_type": "CARD", "created_at": NOW, "updated_at": NOW,
                    "card_details": {"card": {"card_brand": "VISA", "last_4": "0000"}},
                }
            ]
        },
    )

    print(f"Synthetic Bronze fixture written under {ROOT}")


if __name__ == "__main__":
    main()
