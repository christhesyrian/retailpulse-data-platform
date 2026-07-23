import json
from datetime import datetime, timezone

import pytest

from retailpulse.storage import write_raw_page


def test_write_raw_page(tmp_path):
    extracted_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    path = write_raw_page(
        tmp_path,
        "square",
        "orders",
        {"orders": [{"id": "order-1"}]},
        page_number=1,
        run_id="run-abc",
        environment="sandbox",
        extracted_at=extracted_at,
    )

    assert path.exists()
    stored = json.loads(path.read_text())
    assert stored["metadata"]["entity"] == "orders"
    assert stored["metadata"]["run_id"] == "run-abc"
    assert stored["metadata"]["environment"] == "sandbox"
    assert stored["payload"]["orders"][0]["id"] == "order-1"


def test_write_raw_page_never_overwrites(tmp_path):
    extracted_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    kwargs = dict(
        root=tmp_path,
        source="square",
        entity="orders",
        payload={"orders": []},
        page_number=1,
        run_id="same-run",
        environment="sandbox",
        extracted_at=extracted_at,
    )
    write_raw_page(**kwargs)

    with pytest.raises(FileExistsError):
        write_raw_page(**kwargs)


def test_write_raw_page_different_runs_do_not_collide(tmp_path):
    extracted_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    first = write_raw_page(
        tmp_path,
        "square",
        "orders",
        {"orders": []},
        page_number=1,
        run_id="run-a",
        environment="sandbox",
        extracted_at=extracted_at,
    )
    second = write_raw_page(
        tmp_path,
        "square",
        "orders",
        {"orders": []},
        page_number=1,
        run_id="run-b",
        environment="sandbox",
        extracted_at=extracted_at,
    )

    assert first != second
    assert first.exists()
    assert second.exists()
