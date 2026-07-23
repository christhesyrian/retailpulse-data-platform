# RetailPulse Data Platform

A production-style data engineering portfolio project built from a real, operating liquor store's Square point-of-sale data.

## Business problem

The store runs entirely on Square, which means daily sales, catalog, payment, and (later) inventory data already exists — but it's locked inside Square's dashboard and reports. There's no reproducible, queryable history for answering questions like "which products are slow-moving," "how much revenue do discounts and refunds actually cost us," or "how does this week compare to last week." RetailPulse extracts that data into an owned pipeline so those questions can be answered with SQL instead of manual report-clicking.

It's built against a real store on purpose: the data shapes, edge cases, and volume are the ones a real commerce API produces, not a synthetic tutorial dataset. All development and testing happens against the **Square Sandbox** — see [Data privacy strategy](#data-privacy-strategy) below.

## Business questions this project answers

**Sales performance** — daily/weekly/monthly trends, best hours and days, average transaction value, items per basket, weekday vs. weekend, period-over-period comparisons.

**Product performance** — top sellers, category net sales, slow movers, trending items, frequently-bought-together pairs, seasonality.

**Payments and adjustments** — payment method mix, card vs. cash vs. other, revenue lost to discounts and refunds, Square processing fees, unusual refund/discount patterns.

**Inventory** *(future milestone)* — stockout risk, dead stock, turnover by item/category, days of inventory remaining, reorder recommendations.

**Profitability** *(future milestone, after vendor costs are added)* — gross profit and margin by product/category/vendor, and the financial effect of discounts, refunds, and fees.

> **Note:** Square sales data alone is *revenue*, not *profit*. Net sales and revenue are never described as profit in this project — true profit requires item acquisition costs from vendor invoices or a maintained cost dataset, which is a later milestone.

## Architecture

```mermaid
flowchart LR
    A[Square APIs] --> B[Python Extraction]
    B --> C[Bronze Raw JSON]
    C --> D[Silver Normalized Tables]
    D --> E[Gold Fact and Dimension Models]
    E --> F[Dashboards and Forecasting]
```

| Layer | Status | Description |
|---|---|---|
| Square APIs | Implemented (read-only) | Locations, Catalog, Orders, Payments |
| Python Extraction | Implemented | Cursor-paginated, retrying, sandbox-only |
| Bronze Raw JSON | Implemented | Immutable, partitioned, local, Git-ignored |
| Silver Normalized Tables | Implemented | Deduplicated, flattened CSV tables rebuilt from Bronze JSON |
| Gold Fact/Dimension Models | **Not implemented** — future work | `sql/warehouse_schema.sql` documents the target grain |
| Dashboards and Forecasting | **Not implemented** — future work | KPI dashboard, demand forecasting |

## Current milestone: M2 — Silver normalization

M1 (secure Square Sandbox ingestion) is complete. M2 reads Bronze JSON, deduplicates each Square object by its own `updated_at` (falling back to extraction time), flattens nested structures (catalog item + variation + category joins; order line items), and writes clean CSV tables to `data/silver/` — never overwriting Bronze, always rebuilt from it.

## Technologies

Python 3.11+, [httpx](https://www.python-httpx.org/) (HTTP client with retry/backoff), [pydantic](https://docs.pydantic.dev/) + [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) (typed, secret-aware configuration), pytest + ruff (tests/lint), Square REST API `2026-07-15`.

## Data privacy strategy

- `.env` (holds the Square token) is Git-ignored and never committed, logged, or printed.
- Raw Square responses under `data/bronze/` are Git-ignored — only `data/.gitkeep` is tracked.
- This milestone runs against **Square Sandbox exclusively**; no production data is ever touched.
- Anything published from this project publicly (dashboards, screenshots) will use synthetic or aggregated data — never real customer, employee, or payment information.
- Full policy: [`docs/security-and-data-privacy.md`](docs/security-and-data-privacy.md).

## How to run

See [`docs/setup-guide.md`](docs/setup-guide.md) for full setup instructions. Quick reference:

```bash
cp .env.example .env
# open .env and paste your Square SANDBOX token — see docs/setup-guide.md
make install
make doctor
make check
make test
make lint
make security-check
make extract-sandbox
make silver
```

Raw output lands under:

```text
data/bronze/square/<entity>/extracted_date=YYYY-MM-DD/*.json
```

Silver tables land under:

```text
data/silver/locations.csv
data/silver/catalog_items.csv
data/silver/order_lines.csv
data/silver/payments.csv
```

## What's complete

- [x] Secure configuration (`SecretStr` token, `.env` Git-ignored, no secret in logs/errors)
- [x] Square Sandbox client with cursor pagination, timeouts, and bounded retry/backoff for transient errors
- [x] `retailpulse doctor` / `retailpulse check` diagnostics that never reveal the token
- [x] Immutable, partitioned Bronze JSON storage with `run_id` + `environment` metadata
- [x] Unit tests for pagination, retry behavior, config secrecy, and storage immutability
- [x] Local `make security-check` and credential-free GitHub Actions CI
- [x] Silver normalization: dedup by Square object ID, catalog item/variation/category join, order line-item flattening, payment card-detail minimization

## What's next

- M3: Implement `sql/warehouse_schema.sql` as dbt models with tests
- M4: KPI dashboard reconciled against Square's own reporting
- M5: Inventory snapshots and vendor-cost ingestion for margin analysis
- M6: Orchestration, observability, and cloud deployment
- M7: Webhook-driven incremental ingestion
- M8: Demand forecasting and reorder recommendations

See [`docs/project-charter.md`](docs/project-charter.md) for the full project charter and [`docs/data-dictionary.md`](docs/data-dictionary.md) for expected entity fields.
