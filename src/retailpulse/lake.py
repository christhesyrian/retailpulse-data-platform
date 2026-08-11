"""Where the Silver lake lives: a local directory, or an S3 bucket.

Silver is the layer the warehouse reads. Locally it is a directory of Parquet
files and dbt-duckdb reads them straight off disk, which is what makes this
project clonable and runnable with no account anywhere. On S3 it is the same
Parquet files at an `s3://` prefix, read over DuckDB's `httpfs` extension —
the same object-store-plus-query-engine shape a lakehouse has, minus the
cluster.

Nothing else in the pipeline changes. Bronze stays local (it is raw vendor
JSON, and the thing you want cheapest and most boring), the Silver *contents*
are byte-identical either way, and the dbt models never learn where the files
came from, because a source's `external_location` is just a string.

Set `RETAILPULSE_SILVER_DIR` to an `s3://bucket/prefix` URI to switch. The
credentials follow the ordinary AWS conventions, so anything that already
works for the AWS CLI works here:

    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
    AWS_REGION (or AWS_DEFAULT_REGION)
    AWS_ENDPOINT_URL_S3      -- override for MinIO or another S3-compatible store

With no explicit keys, DuckDB's own credential chain is used instead, which
picks up `~/.aws/credentials`, an instance profile, or a role — so nothing has
to be copied into the environment on a machine that is already authenticated.
"""

from __future__ import annotations

import os
from pathlib import Path

S3_SCHEME = "s3://"


def is_s3(location: str | Path) -> bool:
    """Is this location an S3 URI rather than a local path?"""
    return str(location).startswith(S3_SCHEME)


def join(location: str | Path, name: str) -> str | Path:
    """Append a file name, preserving whether this is a URI or a local path."""
    if is_s3(location):
        return f"{str(location).rstrip('/')}/{name}"
    return Path(location) / name


def to_sql_literal(location: str | Path) -> str:
    """Render a location for use inside a DuckDB SQL string literal."""
    return str(location) if is_s3(location) else Path(location).as_posix()


def s3_settings() -> dict[str, str]:
    """The DuckDB `SET` values implied by the AWS environment, if any.

    Returned rather than applied so the same mapping can be handed to dbt,
    which wants them as profile settings instead of as statements.
    """
    settings: dict[str, str] = {
        "s3_region": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    }

    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if key and secret:
        settings["s3_access_key_id"] = key
        settings["s3_secret_access_key"] = secret
        token = os.environ.get("AWS_SESSION_TOKEN")
        if token:
            settings["s3_session_token"] = token

    endpoint = os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL")
    if endpoint:
        # DuckDB wants a bare host[:port]; the AWS convention is a full URL.
        # Path-style addressing because a MinIO or LocalStack endpoint has no
        # per-bucket DNS, so the virtual-host form resolves to nothing.
        settings["s3_endpoint"] = endpoint.split("://", 1)[-1].rstrip("/")
        settings["s3_use_ssl"] = "true" if endpoint.startswith("https://") else "false"
        settings["s3_url_style"] = "path"

    return settings


def configure_duckdb_for_s3(connection) -> None:
    """Prepare a DuckDB connection to read and write `s3://` locations."""
    connection.execute("install httpfs")
    connection.execute("load httpfs")

    settings = s3_settings()
    if "s3_access_key_id" not in settings:
        # No explicit keys: let DuckDB resolve a profile, instance role or SSO
        # session the same way the AWS SDKs would.
        connection.execute(
            "create or replace secret retailpulse_s3 "
            "(type s3, provider credential_chain, region "
            f"'{settings['s3_region']}')"
        )
        settings = {k: v for k, v in settings.items() if k != "s3_region"}

    for name, value in settings.items():
        literal = value if value in ("true", "false") else f"'{value}'"
        connection.execute(f"set {name} = {literal}")
