import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_raw_page(
    root: Path,
    source: str,
    entity: str,
    payload: dict[str, Any],
    page_number: int,
    run_id: str,
    environment: str,
    extracted_at: datetime | None = None,
) -> Path:
    """Write one immutable API response page to a partitioned Bronze path.

    The filename embeds the run id so two runs never collide, even if they
    land in the same microsecond partition.
    """

    extracted_at = extracted_at or datetime.now(timezone.utc)
    partition = extracted_at.strftime("extracted_date=%Y-%m-%d")
    directory = root / source / entity / partition
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = extracted_at.strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"page-{page_number:05d}-{timestamp}-{run_id}.json"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing Bronze file: {path}")

    envelope = {
        "metadata": {
            "source": source,
            "entity": entity,
            "page_number": page_number,
            "extracted_at": extracted_at.isoformat(),
            "run_id": run_id,
            "environment": environment,
        },
        "payload": payload,
    }
    path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return path
