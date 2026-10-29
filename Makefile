.PHONY: install check doctor extract-sandbox seed-sandbox silver dbt-build dbt-docs demo-data demo-warehouse demo-cloud demo-powerbi-export dashboard powerbi-export test lint security-check dagster dagster-validate refresh sync-cloud refresh-all compare-warehouses

# Days of history to re-extract in `make refresh`. Overlapping what you already
# have is free: Bronze is append-only and Silver dedupes to the latest version
# of each record, so a wider window only costs time.
DAYS ?= 14

# Local, credential-free dbt env: DuckDB is a file on disk, not a server.
DBT_ENV = RETAILPULSE_SILVER_DIR=$(CURDIR)/data/silver RETAILPULSE_INPUT_DIR=$(CURDIR)/data/input RETAILPULSE_WAREHOUSE_PATH=$(CURDIR)/data/gold/warehouse.duckdb

# The publishable warehouse: entirely synthetic, and kept in its own directory
# tree so that building it cannot touch the real store's Bronze, Silver, vendor
# costs or local warehouse. That isolation is the whole point — `make demo-data`
# writes synthetic pages into data/bronze, which is harmless on a fresh clone
# and destructive on a machine that holds production extracts, because
# transform-silver reads every page it finds and would merge the two.
#
# DEMO_DAYS is generous on purpose. Year-over-year DAX needs more than a year of
# history to compare against, and the production warehouse has only 383 days —
# so the demo is where time intelligence can actually be demonstrated.
DEMO_DAYS ?= 800
DEMO_DIR = $(CURDIR)/data/demo
DEMO_ENV = RAW_DATA_DIR=$(DEMO_DIR)/bronze \
           RETAILPULSE_SILVER_DIR=$(DEMO_DIR)/silver \
           RETAILPULSE_INPUT_DIR=$(DEMO_DIR)/input \
           RETAILPULSE_WAREHOUSE_PATH=$(DEMO_DIR)/gold/warehouse.duckdb \
           RETAILPULSE_SILVER_SCHEMA=demo_silver \
           RETAILPULSE_REFERENCE_SCHEMA=demo_reference

# A separate dbt target path, which is not housekeeping. dbt's partial-parse
# cache did not notice RETAILPULSE_SILVER_SCHEMA changing, so a demo build and a
# production build sharing dbt/target/ can resolve sources to the other one's
# datasets. Separate paths make that impossible rather than unlikely. Nested
# under target/ so `dbt clean` and the existing .gitignore both already cover it.
DEMO_TARGET_PATH = target/demo

# Cloud credentials live in .env, which these scripts do NOT read on their own:
# the `retailpulse` CLI loads it through pydantic-settings, but the warehouse
# loaders read os.environ and dbt reads env_var(), so both see an empty
# environment when make is run from a shell that never sourced it. `set -a`
# exports what the file defines for the command that follows and nothing more.
#
# The DEMO_ENV prefix is applied after this on purpose — a `VAR=value command`
# prefix overrides an exported value, so .env cannot quietly redirect a demo
# build at the production datasets via its own RAW_DATA_DIR.
LOAD_ENV = set -a && . $(CURDIR)/.env && set +a

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
#
# When an incremental model changes shape — a new column, or a key that changes
# type — a plain build will not rewrite the rows it already has, and the old
# and new definitions end up coexisting in one table. Pass the flag then:
#
#   python3 scripts/sync_warehouses.py --full-refresh
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
	@if [ -d $(CURDIR)/data/bronze ] && \
	    grep -rlq '"environment": "production"' $(CURDIR)/data/bronze 2>/dev/null; then \
	  echo "REFUSING: data/bronze holds pages extracted from production."; \
	  echo "  Synthetic pages written there would be merged into Silver by"; \
	  echo "  transform-silver, which reads every page it finds. Use the isolated"; \
	  echo "  build instead, which writes to data/demo/ and touches nothing here:"; \
	  echo ""; \
	  echo "    make demo-warehouse"; \
	  exit 1; \
	fi
	. .venv/bin/activate && python3 scripts/generate_synthetic_bronze.py data/bronze
	. .venv/bin/activate && RAW_DATA_DIR=data/bronze retailpulse transform-silver
	. .venv/bin/activate && python3 scripts/generate_synthetic_vendor_costs.py
	$(MAKE) dbt-build

dashboard:
	. .venv/bin/activate && $(DBT_ENV) streamlit run dashboard/app.py

# Marts -> powerbi/data/*.parquet, so a Power BI semantic model opens with no
# cloud credentials. The published report reads BigQuery directly; this is the
# path for anyone who cloned the repo and has no account — the same reason the
# dbt profile has a DuckDB target. Pass ARGS='--include-kpi' to also export the
# KPI models the DAX reconciliation compares against.
powerbi-export:
	. .venv/bin/activate && $(DBT_ENV) python3 scripts/export_marts_for_powerbi.py $(ARGS)

# --- the publishable synthetic warehouse -----------------------------------
#
# Everything below builds fake data, and nothing below reads or writes anything
# belonging to the real store. Bronze is regenerated from scratch each time
# (deleted first, because write_raw_page never overwrites and accumulated pages
# from a run with different --days would dedupe against each other).
demo-warehouse:
	rm -rf $(DEMO_DIR)/bronze
	# The warehouse file goes too, not just Bronze. Two kinds of state survive a
	# rebuild otherwise, and both produce failures that look like model bugs:
	# fact_order_line is incremental, so rows built against the previous fixture
	# stay and are never recomputed — when the store timezone changed from UTC,
	# 8,371 stale rows failed assert_local_time_conversion while the new ones
	# passed. And the catalog snapshot would record a regenerated fixture as 218
	# items appearing and the old 16 vanishing, inventing SCD history that never
	# happened. Nothing here is precious: Bronze is regenerated deterministically
	# from SEED on the line below.
	rm -f $(DEMO_DIR)/gold/warehouse.duckdb
	mkdir -p $(DEMO_DIR)/bronze $(DEMO_DIR)/silver $(DEMO_DIR)/input $(DEMO_DIR)/gold
	. .venv/bin/activate && python3 scripts/generate_synthetic_bronze.py \
		$(DEMO_DIR)/bronze --days $(DEMO_DAYS)
	. .venv/bin/activate && $(DEMO_ENV) retailpulse transform-silver
	. .venv/bin/activate && python3 scripts/generate_synthetic_vendor_costs.py \
		--silver-dir $(DEMO_DIR)/silver --out $(DEMO_DIR)/input/vendor_costs.csv
	. .venv/bin/activate && $(DEMO_ENV) python3 scripts/ensure_dbt_inputs.py
	. .venv/bin/activate && $(DEMO_ENV) dbt build --project-dir dbt --profiles-dir dbt \
		--target-path $(DEMO_TARGET_PATH)

# Push the synthetic warehouse to its own BigQuery datasets — demo_silver,
# demo_reference, demo_marts — leaving silver/reference/retailpulse_marts, which
# hold real sales, untouched. This is the dataset a published report reads.
demo-cloud:
	@test -f $(CURDIR)/.env || { \
	  echo "No .env found. The cloud targets need BIGQUERY_PROJECT and"; \
	  echo "GOOGLE_APPLICATION_CREDENTIALS — copy .env.example and fill them in."; \
	  exit 1; }
	# Dropped and rebuilt, not updated in place — same reason demo-warehouse
	# deletes its DuckDB file. The script refuses any dataset not prefixed
	# `demo`, so it cannot reach silver/reference/retailpulse_marts.
	. .venv/bin/activate && $(LOAD_ENV) && python3 scripts/reset_demo_datasets.py --prefix demo
	. .venv/bin/activate && $(LOAD_ENV) && $(DEMO_ENV) \
		python3 scripts/load_silver_to_bigquery.py
	. .venv/bin/activate && $(LOAD_ENV) && $(DEMO_ENV) \
		RETAILPULSE_DBT_TARGET=prod BIGQUERY_DATASET=demo \
		dbt build --project-dir dbt --profiles-dir dbt --target-path $(DEMO_TARGET_PATH)

# Marts from the synthetic warehouse into powerbi/data/, replacing whatever was
# there. These files are safe to publish; the ones `make powerbi-export` writes
# on this machine are not.
demo-powerbi-export:
	. .venv/bin/activate && $(DEMO_ENV) python3 scripts/export_marts_for_powerbi.py $(ARGS)

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
