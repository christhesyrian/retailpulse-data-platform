#!/usr/bin/env python3
"""Seed the Square Sandbox with fake catalog, order, and payment data.

SANDBOX ONLY. Refuses to run against Production. Uses only Square's
documented Sandbox test nonces (e.g. "cnon:card-nonce-ok") — no real card
data, no real customer or employee information, no real vendor/product
names from the operator's actual store. Every write uses a fresh
idempotency key. This script is not imported by, or reachable from, any
extraction or production code path — it is a standalone, optional utility.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from retailpulse.config import Settings  # noqa: E402

FAKE_CATEGORIES = [
    "Beer",
    "Wine",
    "Liquor",
    "Non-Alcoholic and Miscellaneous",
]

# (category, item name, variation name, price in cents) — generic, fake,
# not copied from any real vendor or store inventory.
FAKE_ITEMS = [
    ("Beer", "Sample Craft Lager 6-Pack", "Regular", 1299),
    ("Beer", "Sample Wheat Ale 4-Pack", "Regular", 999),
    ("Wine", "Sample Cabernet Sauvignon", "750ml", 1899),
    ("Wine", "Sample Pinot Grigio", "750ml", 1599),
    ("Liquor", "Sample Vodka", "1L", 2499),
    ("Liquor", "Sample Aged Rum", "750ml", 2999),
    ("Non-Alcoholic and Miscellaneous", "Sample Sparkling Water", "12oz", 199),
    ("Non-Alcoholic and Miscellaneous", "Sample Bottle Opener", "Each", 599),
]

ORDER_LINE_COUNTS = [1, 2, 1, 2, 1, 1]  # six fake orders


def idempotency_key() -> str:
    return uuid.uuid4().hex


def main() -> int:
    settings = Settings()
    if settings.is_production:
        print("REFUSING TO RUN: SQUARE_ENVIRONMENT=production. This utility is Sandbox-only.")
        return 1

    print("Sandbox seed utility")
    print("This will create in your Square SANDBOX test account:")
    print(f"  - {len(FAKE_CATEGORIES)} fake catalog categories")
    print(f"  - {len(FAKE_ITEMS)} fake catalog items (1 variation each)")
    print(f"  - {len(ORDER_LINE_COUNTS)} fake orders")
    print(f"  - {len(ORDER_LINE_COUNTS)} fake payments (Square test nonce, no real money)")
    print("No real customer, employee, or vendor data is used.\n")

    client = httpx.Client(
        base_url=settings.square_base_url,
        headers={
            "Authorization": f"Bearer {settings.square_access_token.get_secret_value()}",
            "Square-Version": settings.square_api_version,
            "Content-Type": "application/json",
            "User-Agent": "retailpulse-seed/0.1.0",
        },
        timeout=30.0,
    )

    try:
        locations = client.get("/v2/locations").json().get("locations", [])
        active = [loc for loc in locations if loc.get("status") == "ACTIVE"]
        if not active:
            print("No active Sandbox location found. Run `retailpulse check` first.")
            return 1
        location_id = active[0]["id"]
        print(f"Using Sandbox location: {active[0].get('name', 'Unnamed')}")

        category_ids = _create_categories(client)
        variation_ids = _create_items(client, category_ids)
        _create_orders_and_payments(client, location_id, variation_ids)

        print("\nSandbox seed complete.")
        return 0
    finally:
        client.close()


def _create_categories(client: httpx.Client) -> dict[str, str]:
    category_ids: dict[str, str] = {}
    for name in FAKE_CATEGORIES:
        body = {
            "idempotency_key": idempotency_key(),
            "object": {"id": f"#category-{name.lower().replace(' ', '-')}", "type": "CATEGORY",
                       "category_data": {"name": name}},
        }
        response = client.post("/v2/catalog/object", json=body)
        response.raise_for_status()
        category_ids[name] = response.json()["catalog_object"]["id"]
    print(f"Created {len(category_ids)} categories.")
    return category_ids


def _create_items(client: httpx.Client, category_ids: dict[str, str]) -> list[str]:
    variation_ids: list[str] = []
    for category, item_name, variation_name, price_cents in FAKE_ITEMS:
        temp_item_id = f"#item-{uuid.uuid4().hex[:8]}"
        body = {
            "idempotency_key": idempotency_key(),
            "object": {
                "id": temp_item_id,
                "type": "ITEM",
                "item_data": {
                    "name": item_name,
                    "categories": [{"id": category_ids[category]}],
                    "variations": [
                        {
                            "id": f"{temp_item_id}-var",
                            "type": "ITEM_VARIATION",
                            "item_variation_data": {
                                "item_id": temp_item_id,
                                "name": variation_name,
                                "pricing_type": "FIXED_PRICING",
                                "price_money": {"amount": price_cents, "currency": "USD"},
                            },
                        }
                    ],
                },
            },
        }
        response = client.post("/v2/catalog/object", json=body)
        response.raise_for_status()
        related = response.json().get("id_mappings", [])
        variation_id = next(
            m["object_id"] for m in related if m["client_object_id"] == f"{temp_item_id}-var"
        )
        variation_ids.append((variation_id, price_cents))
    print(f"Created {len(variation_ids)} catalog items with variations.")
    return variation_ids


def _create_orders_and_payments(
    client: httpx.Client, location_id: str, variations: list[tuple[str, int]]
) -> None:
    orders_created = 0
    payments_created = 0
    for i, line_count in enumerate(ORDER_LINE_COUNTS):
        chosen = [variations[(i + j) % len(variations)] for j in range(line_count)]
        line_items = [
            {"catalog_object_id": variation_id, "quantity": "1"}
            for variation_id, _price in chosen
        ]
        order_body = {
            "idempotency_key": idempotency_key(),
            "order": {"location_id": location_id, "line_items": line_items},
        }
        order_response = client.post("/v2/orders", json=order_body)
        order_response.raise_for_status()
        order = order_response.json()["order"]
        orders_created += 1

        total_money = order["total_money"]
        payment_body = {
            "idempotency_key": idempotency_key(),
            "source_id": "cnon:card-nonce-ok",
            "amount_money": total_money,
            "location_id": location_id,
            "order_id": order["id"],
        }
        payment_response = client.post("/v2/payments", json=payment_body)
        payment_response.raise_for_status()
        payments_created += 1

    print(f"Created {orders_created} orders and {payments_created} payments.")


if __name__ == "__main__":
    sys.exit(main())
