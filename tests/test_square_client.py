import httpx
import pytest
from pydantic import SecretStr

from retailpulse import square_client as square_client_module
from retailpulse.square_client import SquareAPIError, SquareClient


class FakeSettings:
    """Minimal settings stand-in so tests never touch real config or .env."""

    def __init__(self, environment: str = "sandbox") -> None:
        self.square_access_token = SecretStr("fake-test-token-not-real")
        self.square_api_version = "2026-07-15"
        self.square_environment = environment
        self.square_base_url = "https://connect.squareupsandbox.com"

    def require_token(self) -> SecretStr:
        return self.square_access_token


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Retry backoff would otherwise slow the suite down for no benefit.
    monkeypatch.setattr(square_client_module.time, "sleep", lambda _seconds: None)


def _client(handler) -> SquareClient:
    transport = httpx.MockTransport(handler)
    return SquareClient(FakeSettings(), transport=transport)


def test_iter_catalog_single_page_stops_without_cursor():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"objects": [{"id": "item-1"}]})

    with _client(handler) as client:
        pages = list(client.iter_catalog())

    assert len(pages) == 1
    assert len(calls) == 1
    assert pages[0]["objects"][0]["id"] == "item-1"


def test_iter_payments_follows_cursor_across_pages():
    responses = [
        httpx.Response(200, json={"payments": [{"id": "pay-1"}], "cursor": "next-page"}),
        httpx.Response(200, json={"payments": [{"id": "pay-2"}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    with _client(handler) as client:
        pages = list(client.iter_payments("2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"))

    assert len(pages) == 2
    assert pages[0]["payments"][0]["id"] == "pay-1"
    assert pages[1]["payments"][0]["id"] == "pay-2"
    assert "cursor" not in pages[1]


def test_iter_orders_follows_cursor_and_yields_final_page():
    responses = [
        httpx.Response(200, json={"orders": [{"id": "o-1"}], "cursor": "c1"}),
        httpx.Response(200, json={"orders": [{"id": "o-2"}], "cursor": "c2"}),
        httpx.Response(200, json={"orders": [{"id": "o-3"}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    with _client(handler) as client:
        pages = list(client.iter_orders(["loc-1"], "2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"))

    assert [p["orders"][0]["id"] for p in pages] == ["o-1", "o-2", "o-3"]


def test_iter_inventory_counts_follows_cursor():
    responses = [
        httpx.Response(200, json={"counts": [{"catalog_object_id": "v1", "quantity": "5"}],
                                  "cursor": "next"}),
        httpx.Response(200, json={"counts": [{"catalog_object_id": "v2", "quantity": "3"}]}),
    ]
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return responses.pop(0)

    with _client(handler) as client:
        pages = list(client.iter_inventory_counts(["loc-1"]))

    assert len(pages) == 2
    assert pages[0]["counts"][0]["catalog_object_id"] == "v1"
    assert pages[1]["counts"][0]["catalog_object_id"] == "v2"
    # Correct current endpoint (not the deprecated one).
    assert captured[0].url.path == "/v2/inventory/counts/batch-retrieve"


def test_cursor_not_reused_between_independent_runs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "cursor" not in (request.url.params or {})
        return httpx.Response(200, json={"payments": []})

    with _client(handler) as client:
        list(client.iter_payments("2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"))

    # A fresh iterator call must not carry over a cursor from the prior run.
    with _client(handler) as client:
        list(client.iter_payments("2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"))


def test_transient_error_is_retried_then_succeeds():
    responses = [
        httpx.Response(503, text="service unavailable"),
        httpx.Response(200, json={"locations": [{"id": "loc-1"}]}),
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    with _client(handler) as client:
        result = client.list_locations()

    assert len(calls) == 2
    assert result["locations"][0]["id"] == "loc-1"


def test_auth_error_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"errors": [{"detail": "unauthorized"}]})

    with _client(handler) as client:
        with pytest.raises(SquareAPIError):
            client.list_locations()

    assert len(calls) == 1


def test_exhausted_retries_raise_square_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _client(handler) as client:
        with pytest.raises(SquareAPIError):
            client.list_locations()


def test_error_message_never_includes_authorization_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request body")

    with _client(handler) as client:
        with pytest.raises(SquareAPIError) as exc_info:
            client.list_locations()

    message = str(exc_info.value)
    assert "fake-test-token-not-real" not in message
    assert "Authorization" not in message
    assert "Bearer" not in message
