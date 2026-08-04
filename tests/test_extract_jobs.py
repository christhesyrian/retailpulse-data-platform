"""Tests for location resolution.

`list_active_location_ids` exists so the orchestrator can scope a per-day
extract without writing a redundant Bronze snapshot for every partition. The
filtering rule it shares with `extract_locations` is the part worth pinning:
this store has a second, dead location, and including it would widen every
query for no rows.
"""

import json

from retailpulse.extract.jobs import extract_locations, list_active_location_ids

LOCATIONS_PAYLOAD = {
    "locations": [
        {"id": "L-ACTIVE", "status": "ACTIVE", "name": "westminster"},
        {"id": "L-CLOSED", "status": "INACTIVE", "name": "LIQUOR LANE"},
    ]
}


class StubClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def list_locations(self):
        self.calls += 1
        return self._payload


def test_list_active_location_ids_excludes_inactive():
    client = StubClient(LOCATIONS_PAYLOAD)

    assert list_active_location_ids(client) == ["L-ACTIVE"]
    assert client.calls == 1


def test_list_active_location_ids_writes_no_bronze(tmp_path):
    """The whole point: a 365-day backfill must not add 365 identical files."""
    list_active_location_ids(StubClient(LOCATIONS_PAYLOAD))

    assert list(tmp_path.iterdir()) == []


def test_extract_locations_still_writes_bronze_and_filters(tmp_path):
    """The snapshot path keeps its existing behaviour after the refactor."""
    ids = extract_locations(StubClient(LOCATIONS_PAYLOAD), tmp_path, "run-1", "sandbox")

    assert ids == ["L-ACTIVE"]
    written = list(tmp_path.rglob("*.json"))
    assert len(written) == 1

    envelope = json.loads(written[0].read_text())
    # Bronze stays raw: the inactive location is still recorded, because
    # filtering is a downstream decision, not a reason to lose the response.
    assert len(envelope["payload"]["locations"]) == 2
