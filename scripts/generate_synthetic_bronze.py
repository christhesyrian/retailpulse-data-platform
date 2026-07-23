#!/usr/bin/env python3
"""Generate a synthetic Bronze fixture — fully fake, no Square, no network.

Deterministic (fixed RNG seed) so CI and the dashboard demo are
reproducible. Produces a realistic multi-week dataset (one location, a
small liquor-store-style catalog, orders spread across ~6 weeks with
varied items/quantities/discounts, and matching card/cash payments) so
the KPI models and Streamlit dashboard have something meaningful to
show. None of this is real business data.

Usage:
    python3 scripts/generate_synthetic_bronze.py [output_dir] [--days N]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from retailpulse.storage import write_raw_page  # noqa: E402

SEED = 20260722
LOCATION_ID = "loc-synthetic"
TAX_RATE = 0.08
CARD_SHARE = 0.70
CARD_FEE_RATE = 0.026
CARD_FEE_FIXED_CENTS = 10
CARD_BRANDS = ["VISA", "MASTERCARD", "AMERICAN_EXPRESS", "DISCOVER"]

# (category, item name, price cents) — generic fake items, not from any real store.
CATALOG = [
    ("Beer", "Sample Craft Lager 6-Pack", 1299),
    ("Beer", "Sample Wheat Ale 4-Pack", 999),
    ("Beer", "Sample Hazy IPA 6-Pack", 1499),
    ("Beer", "Sample Pilsner 12-Pack", 1899),
    ("Wine", "Sample Cabernet Sauvignon", 1899),
    ("Wine", "Sample Pinot Grigio", 1599),
    ("Wine", "Sample Merlot", 1799),
    ("Wine", "Sample Prosecco", 2199),
    ("Liquor", "Sample Vodka 1L", 2499),
    ("Liquor", "Sample Aged Rum 750ml", 2999),
    ("Liquor", "Sample Blended Whiskey 750ml", 3499),
    ("Liquor", "Sample Silver Tequila 750ml", 3299),
    ("Non-Alcoholic and Miscellaneous", "Sample Sparkling Water 12oz", 199),
    ("Non-Alcoholic and Miscellaneous", "Sample Tonic Water 4-Pack", 699),
    ("Non-Alcoholic and Miscellaneous", "Sample Bottle Opener", 599),
    ("Non-Alcoholic and Miscellaneous", "Sample Ice Bag", 399),
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_catalog_payload() -> tuple[dict, list[dict]]:
    """Return (catalog_payload, variations) where each variation carries its price/category."""
    categories = sorted({cat for cat, _, _ in CATALOG})
    cat_ids = {cat: f"CAT{i:02d}" for i, cat in enumerate(categories)}

    objects: list[dict] = []
    for cat, cid in cat_ids.items():
        objects.append(
            {
                "type": "CATEGORY", "id": cid, "updated_at": _iso(datetime.now(timezone.utc)),
                "is_deleted": False, "category_data": {"name": cat},
            }
        )

    variations = []
    for i, (cat, name, price) in enumerate(CATALOG):
        item_id = f"ITEM{i:04d}"
        var_id = f"VAR{i:04d}"
        now = _iso(datetime.now(timezone.utc))
        objects.append(
            {
                "type": "ITEM", "id": item_id, "updated_at": now, "is_deleted": False,
                "item_data": {
                    "name": name,
                    "categories": [{"id": cat_ids[cat]}],
                    "variations": [
                        {
                            "type": "ITEM_VARIATION", "id": var_id, "updated_at": now,
                            "is_deleted": False,
                            "item_variation_data": {
                                "item_id": item_id, "name": "Regular",
                                "price_money": {"amount": price, "currency": "USD"},
                            },
                        }
                    ],
                },
            }
        )
        objects.append(
            {
                "type": "ITEM_VARIATION", "id": var_id, "updated_at": now, "is_deleted": False,
                "item_variation_data": {
                    "item_id": item_id, "name": "Regular",
                    "price_money": {"amount": price, "currency": "USD"},
                },
            }
        )
        variations.append({"var_id": var_id, "name": name, "price": price})

    return {"objects": objects}, variations


def build_orders_and_payments(
    variations: list[dict], days: int, rng: random.Random
) -> tuple[list[dict], list[dict]]:
    orders: list[dict] = []
    payments: list[dict] = []
    order_seq = 0
    end_date = datetime.now(timezone.utc).date()

    for day_offset in range(days, -1, -1):
        day = end_date - timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        n_orders = rng.randint(4, 8) if is_weekend else rng.randint(2, 5)

        for _ in range(n_orders):
            order_seq += 1
            order_id = f"ORD{order_seq:06d}"
            hour = rng.randint(10, 21)
            created = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59),
                               rng.randint(0, 59), tzinfo=timezone.utc)
            closed = created + timedelta(minutes=rng.randint(1, 4))

            n_lines = rng.randint(1, 4)
            chosen = rng.sample(variations, k=min(n_lines, len(variations)))
            line_items = []
            order_total = 0
            for j, var in enumerate(chosen):
                qty = rng.randint(1, 3)
                gross = var["price"] * qty
                discount = round(gross * rng.uniform(0.05, 0.15)) if rng.random() < 0.2 else 0
                tax = round((gross - discount) * TAX_RATE)
                line_total = gross - discount + tax
                order_total += line_total
                line_items.append(
                    {
                        "uid": f"{order_id}-L{j}", "catalog_object_id": var["var_id"],
                        "name": var["name"], "variation_name": "Regular", "quantity": str(qty),
                        "gross_sales_money": {"amount": gross, "currency": "USD"},
                        "total_discount_money": {"amount": discount, "currency": "USD"},
                        "total_tax_money": {"amount": tax, "currency": "USD"},
                        "total_money": {"amount": line_total, "currency": "USD"},
                    }
                )

            orders.append(
                {
                    "id": order_id, "location_id": LOCATION_ID, "state": "COMPLETED",
                    "created_at": _iso(created), "updated_at": _iso(closed),
                    "closed_at": _iso(closed), "line_items": line_items,
                    "total_money": {"amount": order_total, "currency": "USD"},
                }
            )

            is_card = rng.random() < CARD_SHARE
            payment = {
                "id": f"PAY{order_seq:06d}", "order_id": order_id, "location_id": LOCATION_ID,
                "amount_money": {"amount": order_total, "currency": "USD"},
                "total_money": {"amount": order_total, "currency": "USD"},
                "status": "COMPLETED",
                "created_at": _iso(created + timedelta(seconds=30)),
                "updated_at": _iso(closed),
            }
            if is_card:
                fee = round(order_total * CARD_FEE_RATE) + CARD_FEE_FIXED_CENTS
                payment["source_type"] = "CARD"
                payment["processing_fee"] = [{"amount_money": {"amount": fee, "currency": "USD"}}]
                payment["card_details"] = {
                    "card": {
                        "card_brand": rng.choice(CARD_BRANDS),
                        "last_4": f"{rng.randint(0, 9999):04d}",
                    }
                }
            else:
                payment["source_type"] = "CASH"
            payments.append(payment)

    return orders, payments


def build_inventory_counts(
    variations: list[dict], now: datetime, rng: random.Random
) -> list[dict]:
    """One current IN_STOCK count per variation at the single location.

    A few items are deliberately left low so inventory-position/reorder
    KPIs have something to surface.
    """
    counts = []
    for i, var in enumerate(variations):
        # every 5th item is intentionally low stock
        quantity = rng.randint(2, 8) if i % 5 == 0 else rng.randint(20, 120)
        counts.append(
            {
                "catalog_object_id": var["var_id"],
                "catalog_object_type": "ITEM_VARIATION",
                "location_id": LOCATION_ID,
                "state": "IN_STOCK",
                "quantity": str(quantity),
                "calculated_at": _iso(now),
            }
        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", nargs="?", default="data/bronze")
    parser.add_argument("--days", type=int, default=42, help="Days of history to generate.")
    args = parser.parse_args()

    root = Path(args.output_dir)
    rng = random.Random(SEED)
    now = datetime.now(timezone.utc)

    location_payload = {
        "locations": [
            {
                "id": LOCATION_ID, "name": "Synthetic Test Store", "status": "ACTIVE",
                "timezone": "UTC", "currency": "USD", "country": "US",
                "business_name": "Synthetic Test Store", "merchant_id": "merch-synthetic",
                "created_at": _iso(now - timedelta(days=365)),
            }
        ]
    }
    catalog_payload, variations = build_catalog_payload()
    orders, payments = build_orders_and_payments(variations, args.days, rng)
    inventory = build_inventory_counts(variations, now, rng)

    write_raw_page(root, "square", "locations", location_payload, page_number=1,
                   run_id="synthetic", environment="sandbox")
    write_raw_page(root, "square", "catalog", catalog_payload, page_number=1,
                   run_id="synthetic", environment="sandbox")
    write_raw_page(root, "square", "orders", {"orders": orders}, page_number=1,
                   run_id="synthetic", environment="sandbox")
    write_raw_page(root, "square", "payments", {"payments": payments}, page_number=1,
                   run_id="synthetic", environment="sandbox")
    write_raw_page(root, "square", "inventory", {"counts": inventory}, page_number=1,
                   run_id="synthetic", environment="sandbox")

    print(
        f"Synthetic Bronze fixture written under {root}: "
        f"1 location, {len(variations)} items, {len(orders)} orders, {len(payments)} payments, "
        f"{len(inventory)} inventory counts across {args.days} days."
    )


if __name__ == "__main__":
    main()
