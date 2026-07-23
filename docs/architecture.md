# Architecture

```mermaid
flowchart LR
    A[Square APIs] --> B[Python Extraction]
    B --> C[Bronze Raw JSON]
    C --> D[Silver Normalized Tables]
    D --> E[Gold Fact and Dimension Models]
    E --> F[Dashboards and Forecasting]
```

## Layers

### Square APIs (implemented, read-only)

`src/retailpulse/square_client.py` calls four read-only Square REST endpoints:

- `GET /v2/locations`
- `GET /v2/catalog/list`
- `POST /v2/orders/search`
- `GET /v2/payments`

Requests carry `Authorization: Bearer <token>`, `Square-Version`, and a descriptive `User-Agent`. Transient failures (HTTP 429/500/502/503/504, and transport-level errors) are retried up to 5 attempts with exponential backoff and jitter, honoring `Retry-After` when Square sends it. Authentication and validation errors (4xx other than 429) fail immediately without retry, since retrying them cannot succeed.

### Python Extraction (implemented)

`src/retailpulse/extract/jobs.py` orchestrates one job per entity (locations, catalog, orders, payments), each following Square's cursor pagination until no `cursor` is returned, writing one raw page per API response.

### Bronze Raw JSON (implemented)

`src/retailpulse/storage.py` writes each page as an immutable, metadata-wrapped JSON file:

```text
data/bronze/square/<entity>/extracted_date=YYYY-MM-DD/page-00001-<timestamp>-<run_id>.json
```

```json
{
  "metadata": {
    "source": "square",
    "entity": "orders",
    "page_number": 1,
    "extracted_at": "2026-07-22T12:00:00+00:00",
    "run_id": "a1b2c3d4...",
    "environment": "sandbox"
  },
  "payload": { "...": "raw Square API response, unmodified" }
}
```

The filename embeds a per-run identifier, so two extraction runs never collide even within the same partition, and `write_raw_page` refuses to overwrite an existing path (`FileExistsError`). This directory is Git-ignored; it never leaves the local machine.

### Silver Normalized Tables — not implemented (future work)

Will deduplicate by Square object ID, flatten nested line items, and type/clean fields (e.g., money as integer cents, timestamps as UTC).

### Gold Fact and Dimension Models — not implemented (future work)

`sql/warehouse_schema.sql` documents the target grain (`fact_order_line`, `fact_payment`, `dim_location`, `dim_item`, and — later — `fact_refund`, `fact_inventory_snapshot`, `dim_category`, `dim_date`). Intended to be implemented as dbt models with tests once Silver is validated.

### Dashboards and Forecasting — not implemented (future work)

KPI dashboard reconciled against Square's own reporting, followed by demand forecasting and reorder recommendations.

## Configuration and secrets

`src/retailpulse/config.py` loads settings via `pydantic-settings`, typing the access token as `SecretStr` so it can't leak into a `repr()`, log line, or exception message. `SQUARE_ENVIRONMENT` defaults to `sandbox`; selecting `production` triggers a visible CLI warning before any request is made. See [`security-and-data-privacy.md`](security-and-data-privacy.md) for the full policy.
