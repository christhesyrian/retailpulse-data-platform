from datetime import datetime, timedelta, timezone
from pathlib import Path

from retailpulse.square_client import SquareClient
from retailpulse.storage import write_raw_page


def utc_window(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def extract_locations(
    client: SquareClient, raw_root: Path, run_id: str, environment: str
) -> list[str]:
    payload = client.list_locations()
    write_raw_page(
        raw_root, "square", "locations", payload, page_number=1, run_id=run_id,
        environment=environment,
    )
    return [
        location["id"]
        for location in payload.get("locations", [])
        if location.get("status") == "ACTIVE"
    ]


def extract_orders(
    client: SquareClient,
    raw_root: Path,
    location_ids: list[str],
    begin_time: str,
    end_time: str,
    run_id: str,
    environment: str,
) -> int:
    pages = 0
    for pages, payload in enumerate(
        client.iter_orders(location_ids, begin_time, end_time), start=1
    ):
        write_raw_page(
            raw_root, "square", "orders", payload, page_number=pages, run_id=run_id,
            environment=environment,
        )
    return pages


def extract_payments(
    client: SquareClient,
    raw_root: Path,
    begin_time: str,
    end_time: str,
    run_id: str,
    environment: str,
) -> int:
    pages = 0
    for pages, payload in enumerate(client.iter_payments(begin_time, end_time), start=1):
        write_raw_page(
            raw_root, "square", "payments", payload, page_number=pages, run_id=run_id,
            environment=environment,
        )
    return pages


def extract_catalog(client: SquareClient, raw_root: Path, run_id: str, environment: str) -> int:
    pages = 0
    for pages, payload in enumerate(client.iter_catalog(), start=1):
        write_raw_page(
            raw_root, "square", "catalog", payload, page_number=pages, run_id=run_id,
            environment=environment,
        )
    return pages
