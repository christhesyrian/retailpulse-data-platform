# Python 3.11 specifically, not "3" or "latest".
#
# The ceiling in pyproject.toml is real: no dagster-dbt wheel installs on 3.14,
# and pip's response is to silently backtrack to a 2023-era dagster and drag
# dbt-core down to 1.7 with it. A floating base image would reintroduce exactly
# the class of bug this project already spent a day on.
FROM python:3.11-slim AS base

# - PYTHONDONTWRITEBYTECODE: no .pyc litter in the mounted volumes
# - PYTHONUNBUFFERED: logs appear as the pipeline runs, not when it exits,
#   which matters when the container is doing a multi-minute dbt build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RETAILPULSE_SILVER_DIR=/app/data/silver \
    RETAILPULSE_INPUT_DIR=/app/data/input \
    RETAILPULSE_WAREHOUSE_PATH=/app/data/gold/warehouse.duckdb \
    DAGSTER_HOME=/app/dagster_home

WORKDIR /app

# git is needed because `retailpulse doctor` shells out to `git check-ignore`
# to prove .env is not tracked. Without it the diagnostic reports a false
# negative rather than failing outright, which is worse.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Dependency metadata first, so `pip install` is cached and only re-runs when
# the dependency set actually changes — not on every source edit.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install -e '.[dev,dashboard,orchestration]'

COPY dbt/ ./dbt/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY Makefile ./
COPY .streamlit/ ./.streamlit/

RUN mkdir -p /app/data/bronze /app/data/silver /app/data/gold /app/data/input /app/dagster_home

# No CMD. This image is a toolbox, not a service: compose picks the entrypoint
# per service, because the same environment has to run a one-shot pipeline, a
# long-lived dashboard and an orchestrator.
