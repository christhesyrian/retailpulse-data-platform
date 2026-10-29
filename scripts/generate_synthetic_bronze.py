#!/usr/bin/env python3
"""Generate a synthetic Bronze fixture — fully fake, no Square, no network.

Deterministic (fixed RNG seed) so CI, the Streamlit demo and the publishable
Power BI warehouse are all reproducible. None of this is real business data;
every item name carries a "Sample" prefix and both locations carry a
`loc-synthetic` id so the fixture can never be mistaken for a real extract.

The shape is chosen to exercise the models rather than merely to fill them:

  * **A long-tailed catalog.** ~200 variations built from brand x style, with a
    Zipf-ish popularity weight per variation. A uniform catalog makes every
    "top sellers" ranking a tie broken by noise, which looks like a bug in the
    report rather than a property of the fixture.

  * **Two locations, both on a real timezone.** The single UTC location this
    used to emit meant `assert_local_time_conversion` skipped itself — it
    excludes `timezone = 'UTC'` by design, since a UTC store has no offset to
    check. So the project's most important invariant, the one guarding a bug
    that silently moved a year of evening trade onto the wrong day, was never
    actually exercised by CI. It is now.

  * **Business hours in local time, converted to UTC on the way out.** Square
    records instants in UTC and the store trades on wall clock; generating in
    wall clock and converting is the only ordering that produces a believable
    hour-of-day curve. Generating 10:00-21:00 "UTC" put the evening peak at
    02:00-13:00 local, which is how the fixture managed to look fine while
    telling you nothing.

Usage:
    python3 scripts/generate_synthetic_bronze.py [output_dir] [--days N]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from retailpulse.storage import write_raw_page  # noqa: E402

SEED = 20260722
TAX_RATE = 0.08
CARD_SHARE = 0.70
CARD_FEE_RATE = 0.026
CARD_FEE_FIXED_CENTS = 10
CARD_BRANDS = ["VISA", "MASTERCARD", "AMERICAN_EXPRESS", "DISCOVER"]

# `loc-synthetic` is load-bearing beyond this file: scripts/export_marts_for_powerbi.py
# reads it out of dim_location to decide whether an export is safe to publish.
# Keep the prefix if these are ever renamed.
LOCATION_ID = "loc-synthetic"
STORE_TIMEZONE = "America/Los_Angeles"

# (id, display name, share of orders). Unequal on purpose — two locations
# splitting trade 50/50 makes a location slicer look broken.
LOCATIONS = [
    (LOCATION_ID, "Sample Store — Downtown", 0.62),
    (f"{LOCATION_ID}-2", "Sample Store — Eastside", 0.38),
]

# Opening hour -> relative weight, in STORE LOCAL time. A liquor store does its
# heaviest trade after work; a flat distribution would make the popular-times
# chart a straight line and the weekday/hour models pointless.
HOUR_WEIGHTS = {
    9: 2, 10: 3, 11: 4, 12: 6, 13: 6, 14: 6, 15: 7,
    16: 10, 17: 14, 18: 15, 19: 13, 20: 9, 21: 5,
}

# brand x style, crossed per category. Prices are per-style, nudged per brand so
# that two brands of the same style do not collide on an identical price — real
# catalogs have no such coincidence and `kpi_item_sales` ranking ties look wrong.
CATALOG_SPEC: dict[str, dict[str, list]] = {
    "Beer": {
        "brands": ["Harbor Point", "Iron Gate", "Copper Kettle", "Switchback",
                   "Lantern Row", "Tidewater", "Granite Fork", "Mill House"],
        "styles": [("Hazy IPA", "6-Pack", 1499), ("Pilsner", "12-Pack", 1899),
                   ("Wheat Ale", "4-Pack", 999), ("Amber Lager", "6-Pack", 1299),
                   ("Dry Stout", "4-Pack", 1199), ("Session IPA", "12-Pack", 1799)],
    },
    "Wine": {
        "brands": ["Vallis Ridge", "Stonebridge", "Alder Creek", "Cypress Hollow",
                   "Marrow Lane", "Fenwick Estate", "Dovecote", "Pale Harvest",
                   "Ninebark", "Quarry Bend"],
        "styles": [("Cabernet Sauvignon", "750ml", 1899), ("Pinot Grigio", "750ml", 1599),
                   ("Merlot", "750ml", 1799), ("Prosecco", "750ml", 2199),
                   ("Chardonnay", "750ml", 1699), ("Rosé", "750ml", 1499)],
    },
    "Liquor": {
        "brands": ["Ashgrove", "Black Lantern", "Cold Harbour", "Dunmore",
                   "Eastcliff", "Foxglove", "Greyhaven", "Hollowmere",
                   "Ironwood", "Juniper Mill"],
        "styles": [("Vodka", "1L", 2499), ("Aged Rum", "750ml", 2999),
                   ("Blended Whiskey", "750ml", 3499), ("Silver Tequila", "750ml", 3299),
                   ("London Dry Gin", "750ml", 2799), ("Single Malt", "750ml", 5499),
                   ("Spiced Rum", "1L", 2299)],
    },
    "Non-Alcoholic and Miscellaneous": {
        "brands": ["Clearwell", "Brightwater", "Hearthside", "Keystone",
                   "Northfield", "Pinecrest", "Saltbox", "Westmoor"],
        "styles": [("Sparkling Water", "12oz", 199), ("Tonic Water", "4-Pack", 699),
                   ("Bottle Opener", "Each", 599), ("Ice Bag", "7lb", 399),
                   ("Ginger Beer", "4-Pack", 799)],
    },
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_catalog() -> list[dict]:
    """Cross brands with styles into a deterministic, long-tailed catalog.

    Popularity is assigned by position in a seeded shuffle and decays as 1/rank,
    so the ranking is stable across runs but is not the catalog's own order —
    otherwise every top-sellers list would just be the first N rows of the file.
    """
    rng = random.Random(SEED)
    items: list[dict] = []

    for category, spec in CATALOG_SPEC.items():
        for brand_index, brand in enumerate(spec["brands"]):
            for style, size, base_price in spec["styles"]:
                # +/- up to ~8%, stepped by brand so it is stable and not random
                # per call: the same brand is consistently the cheaper one.
                nudge = ((brand_index % 5) - 2) * 0.04
                price = max(99, int(round(base_price * (1 + nudge) / 25.0)) * 25 - 1)
                items.append({
                    "category": category,
                    "item_name": f"Sample {brand} {style}",
                    "variation_name": size,
                    "price": price,
                })

    rng.shuffle(items)
    for rank, item in enumerate(items, start=1):
        item["weight"] = 1.0 / (rank ** 0.75)
    return items


def build_catalog_payload(catalog: list[dict]) -> tuple[dict, list[dict]]:
    """Return (catalog_payload, variations) where each variation carries price/category/weight."""
    categories = sorted({item["category"] for item in catalog})
    cat_ids = {cat: f"CAT{i:02d}" for i, cat in enumerate(categories)}
    now = _iso(datetime.now(timezone.utc))

    objects: list[dict] = [
        {
            "type": "CATEGORY", "id": cid, "updated_at": now,
            "is_deleted": False, "category_data": {"name": cat},
        }
        for cat, cid in cat_ids.items()
    ]

    variations = []
    for i, item in enumerate(catalog):
        item_id = f"ITEM{i:04d}"
        var_id = f"VAR{i:04d}"
        variation_data = {
            "item_id": item_id, "name": item["variation_name"],
            "price_money": {"amount": item["price"], "currency": "USD"},
        }
        objects.append({
            "type": "ITEM", "id": item_id, "updated_at": now, "is_deleted": False,
            "item_data": {
                "name": item["item_name"],
                "categories": [{"id": cat_ids[item["category"]]}],
                "variations": [{
                    "type": "ITEM_VARIATION", "id": var_id, "updated_at": now,
                    "is_deleted": False, "item_variation_data": variation_data,
                }],
            },
        })
        objects.append({
            "type": "ITEM_VARIATION", "id": var_id, "updated_at": now,
            "is_deleted": False, "item_variation_data": variation_data,
        })
        variations.append({
            "var_id": var_id, "name": item["item_name"],
            "variation_name": item["variation_name"],
            "price": item["price"], "weight": item["weight"],
        })

    return {"objects": objects}, variations


def build_orders_and_payments(
    variations: list[dict], days: int, rng: random.Random
) -> tuple[list[dict], list[dict]]:
    orders: list[dict] = []
    payments: list[dict] = []
    order_seq = 0
    store_tz = ZoneInfo(STORE_TIMEZONE)
    end_date = datetime.now(timezone.utc).astimezone(store_tz).date()

    location_ids = [loc_id for loc_id, _, _ in LOCATIONS]
    location_shares = [share for _, _, share in LOCATIONS]
    hours = list(HOUR_WEIGHTS)
    hour_weights = list(HOUR_WEIGHTS.values())
    var_weights = [v["weight"] for v in variations]

    for day_offset in range(days, -1, -1):
        day = end_date - timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        n_orders = rng.randint(34, 58) if is_weekend else rng.randint(17, 31)

        for _ in range(n_orders):
            order_seq += 1
            order_id = f"ORD{order_seq:06d}"
            location_id = rng.choices(location_ids, weights=location_shares, k=1)[0]

            # Wall clock first, then the instant. See the module docstring.
            hour = rng.choices(hours, weights=hour_weights, k=1)[0]
            created_local = datetime(day.year, day.month, day.day, hour,
                                     rng.randint(0, 59), rng.randint(0, 59),
                                     tzinfo=store_tz)
            created = created_local.astimezone(timezone.utc)
            closed = created + timedelta(minutes=rng.randint(1, 4))

            n_lines = rng.randint(1, 4)
            # Weighted sampling without replacement: one popular item should not
            # appear twice on the same ticket as two separate lines.
            chosen: list[dict] = []
            pool, weights = list(variations), list(var_weights)
            for _ in range(min(n_lines, len(pool))):
                pick = rng.choices(range(len(pool)), weights=weights, k=1)[0]
                chosen.append(pool.pop(pick))
                weights.pop(pick)

            line_items = []
            order_total = 0
            for j, var in enumerate(chosen):
                qty = rng.randint(1, 3)
                gross = var["price"] * qty
                discount = round(gross * rng.uniform(0.05, 0.15)) if rng.random() < 0.2 else 0
                tax = round((gross - discount) * TAX_RATE)
                line_total = gross - discount + tax
                order_total += line_total
                line_items.append({
                    "uid": f"{order_id}-L{j}", "catalog_object_id": var["var_id"],
                    "name": var["name"], "variation_name": var["variation_name"],
                    "quantity": str(qty),
                    "gross_sales_money": {"amount": gross, "currency": "USD"},
                    "total_discount_money": {"amount": discount, "currency": "USD"},
                    "total_tax_money": {"amount": tax, "currency": "USD"},
                    "total_money": {"amount": line_total, "currency": "USD"},
                })

            orders.append({
                "id": order_id, "location_id": location_id, "state": "COMPLETED",
                "created_at": _iso(created), "updated_at": _iso(closed),
                "closed_at": _iso(closed), "line_items": line_items,
                "total_money": {"amount": order_total, "currency": "USD"},
            })

            payment = {
                "id": f"PAY{order_seq:06d}", "order_id": order_id,
                "location_id": location_id,
                "amount_money": {"amount": order_total, "currency": "USD"},
                "total_money": {"amount": order_total, "currency": "USD"},
                "status": "COMPLETED",
                "created_at": _iso(created + timedelta(seconds=30)),
                "updated_at": _iso(closed),
            }
            if rng.random() < CARD_SHARE:
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
    """One current IN_STOCK count per variation, at every location.

    Roughly every fifth row is deliberately left low so the inventory-position
    and reorder KPIs have something to surface rather than a uniform wall of
    healthy stock. The low rows are offset per location so the two stores do not
    run short on the same items — a reorder list identical at both stores would
    make the location filter look broken.

    Both locations carry the full range, which is how two branches of one
    retailer usually work and which `kpi_inventory_position` can now represent.
    Until that model was regrained it could not: it dropped the location and
    asserted `unique` on the catalog object id, so stocking every variation
    twice failed that test once per item. The fixture was narrowed to one
    location per variation to stay inside the grain; the model now carries the
    location through and keys on the pair, so this no longer has to pretend.
    """
    counts = []
    for offset, (loc_id, _, _) in enumerate(LOCATIONS):
        for i, var in enumerate(variations):
            is_low = (i + offset * 2) % 5 == 0
            quantity = rng.randint(2, 8) if is_low else rng.randint(20, 120)
            counts.append({
                "catalog_object_id": var["var_id"],
                "catalog_object_type": "ITEM_VARIATION",
                "location_id": loc_id,
                "state": "IN_STOCK",
                "quantity": str(quantity),
                "calculated_at": _iso(now),
            })
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
                "id": loc_id, "name": name, "status": "ACTIVE",
                "timezone": STORE_TIMEZONE, "currency": "USD", "country": "US",
                "business_name": "Sample Synthetic Retail Co",
                "merchant_id": "merch-synthetic",
                "created_at": _iso(now - timedelta(days=max(args.days, 365) + 30)),
            }
            for loc_id, name, _ in LOCATIONS
        ]
    }
    catalog = build_catalog()
    catalog_payload, variations = build_catalog_payload(catalog)
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

    line_count = sum(len(order["line_items"]) for order in orders)
    print(
        f"Synthetic Bronze fixture written under {root}: "
        f"{len(LOCATIONS)} locations ({STORE_TIMEZONE}), {len(variations)} items, "
        f"{len(orders)} orders, {line_count} order lines, {len(payments)} payments, "
        f"{len(inventory)} inventory counts across {args.days} days."
    )


if __name__ == "__main__":
    main()
