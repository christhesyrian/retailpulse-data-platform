# Setup Guide

## Prerequisites

- Python 3.11 or newer
- Git
- A Square account that owns or manages the store (for Sandbox credentials)

## 1. Clone and install

```bash
make install
```

This creates a `.venv` and installs the project in editable mode with dev dependencies (pytest, ruff).

## 2. Create your local `.env`

```bash
cp .env.example .env
```

Open `.env` in your editor and confirm:

```dotenv
SQUARE_ENVIRONMENT=sandbox
SQUARE_API_VERSION=2026-07-15
RAW_DATA_DIR=data/bronze
```

Leave `SQUARE_ACCESS_TOKEN` as-is for now — you'll fill that in during Square Developer Console setup (see the project's Square setup checkpoint). **Never** put a real token in `.env.example` — only in your local, Git-ignored `.env`.

## 3. Verify configuration

```bash
make doctor
```

This reports Python/package status, the selected environment, whether a token is configured (true/false only — never the value), whether the raw data directory is writable, and whether `.env` is protected by `.gitignore`. It will report `Configuration: FAILED to load` until `SQUARE_ACCESS_TOKEN` is set in `.env`.

## 4. Verify Square connectivity

```bash
make check
```

Connects to Sandbox and lists your Sandbox locations. If this fails, see [Troubleshooting](#troubleshooting) below.

## 5. Run the test suite, lint, and security check

```bash
make test
make lint
make security-check
```

All three must pass before extraction or any Git commit.

## 6. Run the first extraction

```bash
make extract-sandbox
```

Extracts the last 7 days of locations, catalog, orders, and payments into `data/bronze/`. Running it again does not overwrite the previous run's files — each run gets its own `run_id`.

## 7. Normalize to Silver and build the Gold warehouse

```bash
make silver
make dbt-build
```

`make silver` rebuilds `data/silver/*.parquet` from whatever's in `data/bronze/`. `make dbt-build` runs the dbt-duckdb project in `dbt/`, which reads those Parquet files directly as sources and builds `data/gold/warehouse.duckdb` (`dim_location`, `dim_item`, `fact_order_line`, `fact_payment`), running 24 schema tests along the way. Inspect it with `duckdb data/gold/warehouse.duckdb` and `SELECT * FROM main_marts.fact_order_line LIMIT 10;`.

## Troubleshooting

**`Configuration error. Copy .env.example to .env and add your token.`**
`.env` is missing or `SQUARE_ACCESS_TOKEN` is empty. Confirm `.env` exists in the project root and has a non-empty token.

**`Square request failed: status=401...`**
The token is invalid, revoked, or doesn't match `SQUARE_ENVIRONMENT`. A Sandbox token only works when `SQUARE_ENVIRONMENT=sandbox`; a Production token only works with `SQUARE_ENVIRONMENT=production`. Re-check both values in `.env`.

**`Square request failed: status=400...` mentioning the API version**
Square's supported version window has moved. Check the current version at the [Square API changelog](https://developer.squareup.com/docs/changelog/connect) and update `SQUARE_API_VERSION` in `.env` (and `.env.example` for the repo default) if needed.

**No locations returned**
The Sandbox test account may not be provisioned yet. Confirm a Sandbox application and default test account exist in the Square Developer Console.
