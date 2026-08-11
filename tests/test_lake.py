"""Tests for the Silver lake location abstraction.

Silver can live on local disk or on S3, and the pipeline writes it and dbt
reads it through the same string. The tests that matter are the ones about
where that string comes from and what shape it takes, because the failure mode
is silent: a location that is subtly wrong doesn't crash, it writes the lake
somewhere nobody is reading from.
"""

from pathlib import Path

import pytest

from retailpulse import lake


class TestIsS3:
    def test_recognises_an_s3_uri(self):
        assert lake.is_s3("s3://bucket/silver")

    def test_a_local_path_is_not_s3(self):
        assert not lake.is_s3(Path("/data/silver"))
        assert not lake.is_s3("data/silver")

    def test_a_path_merely_containing_s3_is_not_s3(self):
        # A directory called "s3" is a local directory.
        assert not lake.is_s3(Path("/data/s3/silver"))


class TestJoin:
    def test_s3_join_keeps_the_uri_a_string(self):
        assert lake.join("s3://bucket/silver", "orders.parquet") == (
            "s3://bucket/silver/orders.parquet"
        )

    def test_s3_join_does_not_double_the_separator(self):
        assert lake.join("s3://bucket/silver/", "orders.parquet") == (
            "s3://bucket/silver/orders.parquet"
        )

    def test_local_join_returns_a_path(self):
        joined = lake.join(Path("/data/silver"), "orders.parquet")
        assert isinstance(joined, Path)
        assert joined == Path("/data/silver/orders.parquet")


class TestToSqlLiteral:
    def test_s3_uri_is_passed_through(self):
        assert lake.to_sql_literal("s3://bucket/x.parquet") == "s3://bucket/x.parquet"

    def test_local_path_is_posix_even_on_windows_style_input(self):
        # DuckDB wants forward slashes inside the SQL string literal.
        assert "\\" not in lake.to_sql_literal(Path("data") / "silver" / "x.parquet")


class TestS3Settings:
    @pytest.fixture(autouse=True)
    def _clear_aws_env(self, monkeypatch):
        for name in (
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_defaults_to_us_east_1_with_no_credentials(self):
        settings = lake.s3_settings()
        assert settings == {"s3_region": "us-east-1"}

    def test_explicit_keys_are_used_when_present(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")
        settings = lake.s3_settings()
        assert settings["s3_access_key_id"] == "AKIAEXAMPLE"
        assert settings["s3_secret_access_key"] == "shh"

    def test_a_key_without_its_secret_is_ignored(self, monkeypatch):
        # Half a credential is not a credential; fall through to the chain
        # rather than sending a request that will certainly be rejected.
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        assert "s3_access_key_id" not in lake.s3_settings()

    def test_session_token_only_travels_with_a_key_pair(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "temp")
        assert lake.s3_settings()["s3_session_token"] == "temp"

    def test_aws_region_wins_over_default_region(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        assert lake.s3_settings()["s3_region"] == "us-west-2"

    def test_endpoint_override_is_reduced_to_a_bare_host(self, monkeypatch):
        # DuckDB wants host[:port]; the AWS convention is a full URL.
        monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://localhost:9100/")
        settings = lake.s3_settings()
        assert settings["s3_endpoint"] == "localhost:9100"

    def test_a_plain_http_endpoint_disables_ssl_and_uses_path_style(self, monkeypatch):
        # MinIO and LocalStack have no per-bucket DNS, so the virtual-host
        # form resolves to nothing.
        monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://localhost:9100")
        settings = lake.s3_settings()
        assert settings["s3_use_ssl"] == "false"
        assert settings["s3_url_style"] == "path"

    def test_an_https_endpoint_keeps_ssl_on(self, monkeypatch):
        monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://s3.example.com")
        assert lake.s3_settings()["s3_use_ssl"] == "true"

    def test_no_endpoint_override_means_no_endpoint_settings(self):
        # Real S3: DuckDB's own defaults are correct, so say nothing.
        settings = lake.s3_settings()
        assert "s3_endpoint" not in settings
        assert "s3_url_style" not in settings
