import random
import time
from collections.abc import Iterator
from typing import Any

import httpx

from retailpulse.config import Settings

# Retryable: rate limiting and transient server-side failures.
# Not retryable: auth/validation errors (400/401/403/404) — retrying those
# just repeats a request that will never succeed.
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5
_BASE_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 8.0


class SquareAPIError(RuntimeError):
    pass


class SquareClient:
    """Small read-only Square REST client for the first RetailPulse milestone."""

    def __init__(
        self,
        settings: Settings,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.square_base_url,
            headers={
                "Authorization": f"Bearer {settings.require_token().get_secret_value()}",
                "Square-Version": settings.square_api_version,
                "Content-Type": "application/json",
                "User-Agent": "retailpulse/0.1.0",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "SquareClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: SquareAPIError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self.client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = SquareAPIError(f"Square request failed: transport error ({exc!r})")
                if attempt == _MAX_ATTEMPTS:
                    raise last_error from exc
                self._sleep_before_retry(attempt, retry_after=None)
                continue

            if not response.is_error:
                return response.json()

            request_id = response.headers.get("x-request-id", "unknown")
            # Truncate and never include request/response headers (which
            # carry the Authorization token) in the error message.
            safe_body = response.text[:300]
            error = SquareAPIError(
                f"Square request failed: status={response.status_code}, "
                f"request_id={request_id}, body={safe_body}"
            )

            if response.status_code not in _TRANSIENT_STATUS_CODES or attempt == _MAX_ATTEMPTS:
                raise error

            last_error = error
            retry_after = response.headers.get("retry-after")
            self._sleep_before_retry(attempt, retry_after=retry_after)

        assert last_error is not None  # pragma: no cover - loop always returns or raises
        raise last_error

    @staticmethod
    def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = _BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        else:
            delay = _BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        delay = min(delay, _MAX_DELAY_SECONDS)
        delay += random.uniform(0, 0.25)
        time.sleep(delay)

    def list_locations(self) -> dict[str, Any]:
        return self._request("GET", "/v2/locations")

    def iter_payments(
        self,
        begin_time: str,
        end_time: str,
        location_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "begin_time": begin_time,
            "end_time": end_time,
            "sort_order": "ASC",
            "sort_field": "UPDATED_AT",
            "limit": 100,
        }
        if location_id:
            params["location_id"] = location_id

        while True:
            page = self._request("GET", "/v2/payments", params=params)
            yield page
            cursor = page.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor

    def iter_orders(
        self,
        location_ids: list[str],
        begin_time: str,
        end_time: str,
    ) -> Iterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "location_ids": location_ids,
            "query": {
                "filter": {
                    "date_time_filter": {
                        "updated_at": {
                            "start_at": begin_time,
                            "end_at": end_time,
                        }
                    }
                },
                "sort": {
                    "sort_field": "UPDATED_AT",
                    "sort_order": "ASC",
                },
            },
            "limit": 500,
            "return_entries": False,
        }

        while True:
            page = self._request("POST", "/v2/orders/search", json=body)
            yield page
            cursor = page.get("cursor")
            if not cursor:
                break
            body["cursor"] = cursor

    def iter_catalog(self) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"types": "ITEM,ITEM_VARIATION,CATEGORY"}
        while True:
            page = self._request("GET", "/v2/catalog/list", params=params)
            yield page
            cursor = page.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
