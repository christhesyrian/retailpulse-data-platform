# Switching to Production (read-only validation)

RetailPulse is Sandbox-only by default. This guide covers pointing it at your
**real** Square store to validate the pipeline on real data. Every Square
operation in RetailPulse is **read-only** — it reads your data and writes
nothing back to Square (no records created, modified, refunded, or deleted).

## Why this is low-risk

- **Read-only.** The client only calls GET/search endpoints (locations,
  catalog, orders, payments, inventory counts). There is no production write
  path in the code.
- **Everything stays local.** Bronze/Silver/Gold and the DuckDB warehouse are
  all under `data/` and Git-ignored. `make security-check` and CI both fail if
  any real data becomes tracked.
- **The token never leaves your machine.** It lives only in local `.env` as a
  `SecretStr`; it is never logged, printed, or committed.
- **Deliberate opt-in.** Even with a production token in `.env`, commands refuse
  to run against production unless you also set `RETAILPULSE_ALLOW_PRODUCTION=1`
  for that specific command. A leftover env value can't silently hit the real
  store.

## Pre-flight checklist

- [ ] `make test`, `make lint`, `make security-check` all pass.
- [ ] `git status` is clean and `git ls-files data/` shows only `data/.gitkeep`.
- [ ] You have a **production** access token from the Square Developer Console
      for the application that manages your store.

## Steps

1. Create a production token in the Square Developer Console (Credentials, with
   the environment toggle set to **Production**). Do **not** paste it into chat,
   a shell command, or any file other than `.env`.

2. In your local `.env`, set:

   ```dotenv
   SQUARE_ENVIRONMENT=production
   SQUARE_ACCESS_TOKEN=<your-production-token>
   ```

3. Verify connectivity (read-only; lists your real locations):

   ```bash
   RETAILPULSE_ALLOW_PRODUCTION=1 retailpulse check
   ```

4. Run a small extraction window and build the warehouse:

   ```bash
   RETAILPULSE_ALLOW_PRODUCTION=1 retailpulse extract-all --days 7
   make silver
   make dbt-build
   ```

5. Validate on real data:
   - Confirm the extraction summary shows non-zero orders/payments/inventory.
   - Confirm `dbt build` passes, including the reconciliation test
     (`assert_order_payment_reconciled`) — real orders and payments should tie.
   - Inspect `main_marts.kpi_summary` and `kpi_inventory_position` in
     `duckdb data/gold/warehouse.duckdb` and sanity-check against what you know
     of the store.

6. **Switch back to Sandbox** for further development:

   ```dotenv
   SQUARE_ENVIRONMENT=sandbox
   SQUARE_ACCESS_TOKEN=<your-sandbox-token>
   ```

## What stays out of scope

- **No production writes.** RetailPulse never writes to Square. The Sandbox-only
  seed utility (`scripts/seed_sandbox.py`) hard-refuses to run against production.
- **No real data in the repo or dashboards.** Anything shared publicly uses the
  synthetic generator. Vendor costs and any real extract stay in Git-ignored
  `data/`.
- **External reconciliation** against Square's Reporting API/Dashboard totals is
  a future enhancement; the built-in reconciliation is internal (pipeline vs.
  itself).
