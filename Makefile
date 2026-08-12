.PHONY: install check doctor extract-sandbox seed-sandbox silver dbt-build dbt-docs demo-data dashboard test lint security-check dagster dagster-validate refresh sync-cloud refresh-all compare-warehouses

# Days of history to re-extract in `make refresh`. Overlapping what you already
# have is free: Bronze is append-only and Silver dedupes to the latest version
# of each record, so a wider window only costs time.
DAYS ?= 14

# Local, credential-free dbt env: DuckDB is a file on disk, not a server.
DBT_ENV = RETAILPULSE_SILVER_DIR=$(CURDIR)/data/silver RETAILPULSE_INPUT_DIR=$(CURDIR)/data/input RETAILPULSE_WAREHOUSE_PATH=$(CURDIR)/data/gold/warehouse.duckdb

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e '.[dev,dashboard]'

check:
	. .venv/bin/activate && retailpulse check

doctor:
	. .venv/bin/activate && retailpulse doctor

extract-sandbox:
	. .venv/bin/activate && retailpulse extract-all --days 7

seed-sandbox:
	. .venv/bin/activate && python3 scripts/seed_sandbox.py

silver:
	. .venv/bin/activate && retailpulse transform-silver

dbt-build:
	. .venv/bin/activate && $(DBT_ENV) python3 scripts/ensure_dbt_inputs.py
	. .venv/bin/activate && $(DBT_ENV) dbt build --project-dir dbt --profiles-dir dbt

dbt-docs:
	. .venv/bin/activate && $(DBT_ENV) dbt docs generate --project-dir dbt --profiles-dir dbt

# --- refreshing real data ---------------------------------------------------
#
# `refresh` is the everyday one: pull the last $(DAYS) days from Square, rebuild
# Silver, rebuild the local warehouse. Against a production token this still
# needs RETAILPULSE_ALLOW_PRODUCTION=1 for the extract step, so running it by
# accident stops with an explanation rather than calling the real store.
#
#   make refresh                 # 14 days
#   make refresh DAYS=30
refresh:
	. .venv/bin/activate && retailpulse extract-all --days $(DAYS)
	$(MAKE) silver
	$(MAKE) dbt-build

# Bring Snowflake and BigQuery level with the Silver that `refresh` just built.
# Warehouses with no credentials in the environment are skipped, not failed.
sync-cloud:
	. .venv/bin/activate && $(DBT_ENV) python3 scripts/sync_warehouses.py

# Everything: pull, rebuild locally, push to both clouds, then prove the three
# still agree on the answers rather than merely on having built.
refresh-all:
	$(MAKE) refresh
	$(MAKE) sync-cloud
	$(MAKE) compare-warehouses

compare-warehouses:
	. .venv/bin/activate && $(DBT_ENV) python3 scripts/compare_warehouses.py

# One command to build the full demo warehouse from synthetic data (no Square,
# no token needed — transform-silver is a purely local Bronze->Silver step).
# Vendor costs are generated from the Silver catalog into data/input/ (git-ignored).
demo-data:
	. .venv/bin/activate && python3 scripts/generate_synthetic_bronze.py data/bronze
	. .venv/bin/activate && RAW_DATA_DIR=data/bronze retailpulse transform-silver
	. .venv/bin/activate && python3 scripts/generate_synthetic_vendor_costs.py
	$(MAKE) dbt-build

dashboard:
	. .venv/bin/activate && $(DBT_ENV) streamlit run dashboard/app.py

# Dagster's UI at localhost:3000: asset graph, run history, backfills.
# DAGSTER_HOME must be absolute and must exist, or Dagster falls back to a
# temporary instance and silently forgets every run when the process exits.
dagster:
	mkdir -p $(CURDIR)/dagster_home
	. .venv/bin/activate && $(DBT_ENV) DAGSTER_HOME=$(CURDIR)/dagster_home \
		dagster dev -m retailpulse.orchestration.definitions

# Loads the whole asset graph without running anything — the same check CI does.
dagster-validate:
	. .venv/bin/activate && $(DBT_ENV) dbt parse --project-dir dbt --profiles-dir dbt
	. .venv/bin/activate && $(DBT_ENV) dagster definitions validate -m retailpulse.orchestration.definitions

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check .

security-check:
	. .venv/bin/activate && python3 scripts/security_check.py
