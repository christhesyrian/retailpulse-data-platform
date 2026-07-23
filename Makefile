.PHONY: install check doctor extract-sandbox seed-sandbox silver dbt-build dbt-docs demo-data dashboard test lint security-check

# Local, credential-free dbt env: DuckDB is a file on disk, not a server.
DBT_ENV = RETAILPULSE_SILVER_DIR=$(CURDIR)/data/silver RETAILPULSE_WAREHOUSE_PATH=$(CURDIR)/data/gold/warehouse.duckdb

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
	mkdir -p data/gold
	. .venv/bin/activate && $(DBT_ENV) dbt build --project-dir dbt --profiles-dir dbt

dbt-docs:
	. .venv/bin/activate && $(DBT_ENV) dbt docs generate --project-dir dbt --profiles-dir dbt

# One command to build the full demo warehouse from synthetic data (no Square,
# no token needed — transform-silver is a purely local Bronze->Silver step).
demo-data:
	. .venv/bin/activate && python3 scripts/generate_synthetic_bronze.py data/bronze
	. .venv/bin/activate && RAW_DATA_DIR=data/bronze retailpulse transform-silver
	$(MAKE) dbt-build

dashboard:
	. .venv/bin/activate && $(DBT_ENV) streamlit run dashboard/app.py

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check .

security-check:
	. .venv/bin/activate && python3 scripts/security_check.py
