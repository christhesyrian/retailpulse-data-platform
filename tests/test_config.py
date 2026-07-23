import pytest

from retailpulse.config import Settings


def test_missing_token_is_allowed_but_require_token_raises(monkeypatch, tmp_path):
    # Local steps (transform-silver, dbt, dashboard) don't need a token, so
    # Settings must construct fine without one...
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file here
    settings = Settings(_env_file=None)
    assert settings.has_token is False

    # ...but any command that contacts Square must fail clearly.
    with pytest.raises(RuntimeError, match="SQUARE_ACCESS_TOKEN is not set"):
        settings.require_token()


def test_require_token_returns_secret_when_present(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "fake-token")
    settings = Settings(_env_file=None)
    assert settings.has_token is True
    assert settings.require_token().get_secret_value() == "fake-token"


def test_token_never_appears_in_repr_or_str(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "super-secret-value-123")
    settings = Settings(_env_file=None)
    assert "super-secret-value-123" not in repr(settings)
    assert "super-secret-value-123" not in str(settings)


def test_sandbox_is_default_environment(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "fake-token")
    monkeypatch.delenv("SQUARE_ENVIRONMENT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.square_environment == "sandbox"
    assert settings.is_production is False
    assert "squareupsandbox.com" in settings.square_base_url


def test_production_environment_maps_to_production_url(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    settings = Settings(_env_file=None)
    assert settings.is_production is True
    assert settings.square_base_url == "https://connect.squareup.com"
