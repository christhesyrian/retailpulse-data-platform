# RetailPulse Project Charter

## Problem
Square contains the store's operational data, but decision-making is limited by manual reports, disconnected inventory information, and a lack of reproducible historical models.

## Product
RetailPulse is a production-style retail data platform that extracts Square commerce data, stores immutable raw records, transforms them into tested analytical models, and exposes business metrics for sales, inventory, payments, and operations.

## Initial business questions
1. Which products and categories drive net sales by day, hour, and location?
2. How do discounts, refunds, taxes, and processing fees affect collected revenue?
3. Which products are accelerating, slowing, or at risk of stocking out?
4. What is the average transaction value and units per basket?
5. Can pipeline-derived totals reconcile to Square's Reporting API and Dashboard?

## MVP source scope
- Locations
- Catalog items, variations, categories, and prices
- Orders and line items
- Payments and processing fees

## Deferred scope
- Customer PII
- Team-member performance
- Real-time webhooks
- Vendor costs and purchase orders
- Forecasting and machine learning

## Engineering requirements
- Credentials never appear in Git history.
- Bronze responses are immutable and partitioned by extraction date.
- Pagination is supported.
- Incremental extraction uses updated timestamps and a safety overlap window.
- Money remains integer cents until the presentation layer.
- Source IDs are preserved for deduplication and lineage.
- Every Gold metric has a documented definition and reconciliation check.

## Definition of done for Milestone 1
- Sandbox connection succeeds.
- Seven days of locations, catalog, orders, and payments are extracted.
- Raw response pages are written under `data/bronze`.
- Re-running the extraction does not overwrite previous raw files.
- Tests pass and no secrets are committed.
